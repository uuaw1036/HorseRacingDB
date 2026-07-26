# -*- coding: utf-8 -*-
"""
netkeiba 持ちタイム取得スクリプト (Google Colab用)
------------------------------------------------
【重要・自己責任でご利用ください】
netkeibaの利用規約では、サービス運営に支障をきたすようなアクセス
(短時間の大量リクエストなど)は禁止される場合があります。
本コードは個人利用・学習目的を想定し、以下に配慮しています。
  - 1リクエストごとに待機時間(time.sleep)を入れる
  - 過度な並列アクセスは行わない
アクセス制限(アク禁)を受ける可能性もあるため、大量のデータを
一気に取得しようとせず、少しずつ実行することをおすすめします。

必要なライブラリ (Colabなら大抵インストール済み):
    !pip install requests beautifulsoup4 lxml
------------------------------------------------
使い方の流れ:
  1. 出走予定馬の netkeiba 馬ID を用意する
     (例: https://db.netkeiba.com/horse/2002100816/ の "2002100816" 部分。
      これは「プロフィール」ページのURLで、成績データ自体は
      https://db.netkeiba.com/horse/result/2002100816/ から取得します)
  2. get_horse_past_results() で各馬の過去レース成績を取得
  3. compare_times.py の関数と組み合わせて持ちタイムを比較
------------------------------------------------
"""

import re
import time
import requests
import pandas as pd
from io import StringIO
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

# リクエスト間の待機秒数(サーバー負荷軽減のため。短くしすぎない)
WAIT_SECONDS = 1.0


def parse_distance_column(value: str):
    """
    netkeibaの「距離」列は "芝2500" や "ダ1600" のように
    馬場種別(芝/ダート)と距離(m)が結合された文字列になっている。
    これを (馬場種別, 距離数値) に分離する。
    """
    value = str(value).strip()
    surface = value[0] if value and value[0] in ("芝", "ダ", "障") else None
    digits = "".join(ch for ch in value if ch.isdigit())
    distance = int(digits) if digits else None
    return surface, distance


def _extract_horse_name_from_title(title_text: str, fallback: str) -> str:
    """
    <title>タグの文字列から馬名だけを取り出す共通処理。
    例: "ディープインパクト (Deep Impact)の競走成績 | 競走馬データ - netkeiba"
        -> "ディープインパクト"
    """
    text = title_text.strip()
    if not text:
        return fallback

    if "の競走成績" in text:
        text = text.split("の競走成績")[0]
    elif "|" in text:
        text = text.split("|")[0]

    name = text.split("(")[0].strip()
    return name if name else fallback


def get_horse_name(horse_id: str) -> str:
    """
    netkeibaの成績ページから馬名だけを取得する(単体で使う場合用)。

    注意: get_multiple_horses()/get_horse_past_results() を使う場合は
    その中で馬名も一緒に取得済みなので、これを別途呼ぶと同じページに
    二重にアクセスすることになる。単体で馬名だけ知りたい時のみ使うこと。
    """
    url = f"https://db.netkeiba.com/horse/result/{horse_id}/"
    response = requests.get(url, headers=HEADERS)
    response.raise_for_status()
    response.encoding = "euc-jp"

    soup = BeautifulSoup(response.text, "lxml")
    title_tag = soup.find("title")
    if title_tag is None:
        return horse_id
    return _extract_horse_name_from_title(title_tag.text, horse_id)


def get_race_entries(race_id: str) -> pd.DataFrame:
    """
    レースの出馬表ページから、出走馬の horse_id と馬名の一覧を取得する。

    URL: https://race.netkeiba.com/race/shutuba.html?race_id={race_id}
    race_id の調べ方: netkeibaでレースページを開き、URLの "race_id=" の
    後ろの数字部分をコピーする(例: race_id=202506050812)。

    ※ このページは UTF-8 エンコード。db.netkeiba.com(EUC-JP)とは異なるので注意。
    """
    url = f"https://race.netkeiba.com/race/shutuba.html?race_id={race_id}"
    response = requests.get(url, headers=HEADERS)
    response.raise_for_status()
    response.encoding = "utf-8"

    soup = BeautifulSoup(response.text, "lxml")

    seen = {}
    for a in soup.find_all("a", href=True):
        match = re.search(r"/horse/(\d{8,12})/?(?:[\"?]|$)", a["href"])
        if not match:
            continue
        horse_id = match.group(1)
        if horse_id in seen:
            continue  # ページ内に同じ馬へのリンクが複数出るため重複除外
        name = a.get("title") or a.text.strip()
        if name:
            seen[horse_id] = name

    if not seen:
        raise ValueError(
            f"出走馬が見つかりませんでした (race_id={race_id})。"
            "race_idが正しいか、レースがまだ発表されていない可能性があります。"
        )

    return pd.DataFrame({"horse_id": list(seen.keys()), "馬名": list(seen.values())})


def get_race_list(date: str) -> pd.DataFrame:
    """
    指定した開催日に行われるレースの一覧(race_id・開催場・R番号・
    発走時刻・レース名)を取得する。

    date: "YYYYMMDD" 形式の文字列 (例: "20250504")。"2025-05-04" や
          "2025/05/04" のようにハイフン・スラッシュ入りで渡しても
          自動的に取り除いて解釈する。

    URL: https://race.netkeiba.com/top/race_list.html?kaisai_date={date}

    ※ 注意: このページのHTML構造(クラス名など)はサイト改修で変わる
      ことがある。まず想定される構造(RaceList_DataItem など)で
      抽出を試み、うまく取れなかった場合は「race_id を含むリンクを
      片っ端から拾う」汎用フォールバックに切り替える。それでも
      0件の場合は、その日開催が無いか、サイト構造が大きく変わった
      可能性が高い(その場合は app.py の「レースIDを直接入力」タブ
      から従来通りレースIDを手入力してください)。
    """
    date = str(date).strip().replace("-", "").replace("/", "")
    if not re.fullmatch(r"\d{8}", date):
        raise ValueError(f"日付は YYYYMMDD 形式で指定してください(例: 20250504): {date}")

    url = f"https://race.netkeiba.com/top/race_list.html?kaisai_date={date}"
    response = requests.get(url, headers=HEADERS)
    response.raise_for_status()
    response.encoding = "utf-8"  # race.netkeiba.com は UTF-8

    soup = BeautifulSoup(response.text, "lxml")

    races = {}

    # --- 1) 構造化された抽出を試みる ---------------------------------
    # 開催(競馬場)のブロック <dl class="RaceList_DataList"> ごとに、
    # ヘッダー(.RaceList_DataTitle: 例 "2回 福島 3日目")と、その中の
    # 各レース <li class="RaceList_DataItem"> を辿る。
    for block in soup.select("dl.RaceList_DataList"):
        venue = ""
        title_el = block.select_one(".RaceList_DataTitle")
        if title_el:
            venue = title_el.get_text(" ", strip=True)

        for item in block.select("li.RaceList_DataItem"):
            a = item.find("a", href=True)
            if not a:
                continue
            m = re.search(r"race_id=(\d{10,12})", a["href"])
            if not m:
                continue
            race_id = m.group(1)

            num_el = item.select_one(".Race_Num")
            race_num = num_el.get_text(strip=True) if num_el else ""

            name_el = item.select_one(".RaceList_ItemTitle .ItemTitle")
            race_name = name_el.get_text(strip=True) if name_el else ""

            time_el = item.select_one(".RaceList_Itemtime")
            post_time = time_el.get_text(strip=True) if time_el else ""

            # 距離表示は "芝1800m"/"ダ1700m"(class=RaceList_ItemLong)のほか、
            # 障害戦は class 無しの "障2860m" になっているため、
            # RaceDataブロック全体のテキストから正規表現で拾う。
            distance = ""
            race_data_el = item.select_one(".RaceData")
            if race_data_el:
                dist_m = re.search(r"[芝ダ障]\d{3,4}m", race_data_el.get_text())
                if dist_m:
                    distance = dist_m.group(0)

            races[race_id] = {
                "race_id": race_id,
                "開催": venue,
                "R": race_num,
                "発走時刻": post_time,
                "距離": distance,
                "レース名": race_name,
            }

    # --- 2) 何も取れなかった場合、リンクの汎用走査にフォールバック ----
    if not races:
        for a in soup.find_all("a", href=True):
            m = re.search(r"race_id=(\d{10,12})", a["href"])
            if not m:
                continue
            race_id = m.group(1)
            if race_id in races:
                continue
            text = a.get_text(strip=True)
            if not text:
                continue
            time_m = re.search(r"\d{1,2}:\d{2}", text)
            races[race_id] = {
                "race_id": race_id,
                "開催": "",
                "R": "",
                "発走時刻": time_m.group(0) if time_m else "",
                "距離": "",
                "レース名": text,
            }

    if not races:
        raise ValueError(
            f"{date} のレースが見つかりませんでした。開催が無い日か、"
            "サイトのHTML構造が変わっている可能性があります。"
            "その場合はレースIDを直接入力する方法をお試しください。"
        )

    df = pd.DataFrame(list(races.values()))
    return df.sort_values(["開催", "発走時刻"]).reset_index(drop=True)


def get_horse_past_results(horse_id: str) -> pd.DataFrame:
    """
    netkeibaの「競走成績」ページから過去のレース成績表を取得する。

    重要: プロフィールのトップページ (https://db.netkeiba.com/horse/{id}/) には
    成績表が無い。成績表があるのは以下のURL:
        https://db.netkeiba.com/horse/result/{id}/  ← "result" が必要

    戻り値の主な列: 日付・開催・レース名・距離・馬場・タイム など
    ※ サイト改修でテーブルの列構成が変わることがあるため、クラス名指定では
      なく「タイム列を含むテーブルを探す」方式にして壊れにくくしている。
    """
    url = f"https://db.netkeiba.com/horse/result/{horse_id}/"
    response = requests.get(url, headers=HEADERS)
    response.raise_for_status()
    response.encoding = "euc-jp"  # netkeibaはEUC-JPエンコードのページが多い

    # 馬名は同じレスポンスの<title>タグから取得する(二重アクセスしない)
    soup = BeautifulSoup(response.text, "lxml")
    title_tag = soup.find("title")
    horse_name = (
        _extract_horse_name_from_title(title_tag.text, horse_id)
        if title_tag is not None
        else horse_id
    )

    try:
        # pandasのバージョンによっては文字列を直接渡すとエラーになるため
        # StringIOで包んで渡す
        tables = pd.read_html(StringIO(response.text))
    except ValueError:
        tables = []

    df = None
    for t in tables:
        if "タイム" in t.columns:
            df = t
            break

    if df is None:
        raise ValueError(
            f"過去成績テーブルが見つかりませんでした (horse_id={horse_id})。"
            "新馬(未出走)の可能性、またはサイトのHTML構造が変わっている可能性があります。"
        )

    # 列名に空白(半角/全角)が混入することがあるため正規化する
    # 例: "馬 場" -> "馬場", "頭 数" -> "頭数"
    df.columns = [str(c).replace(" ", "").replace("　", "") for c in df.columns]

    # タイムが空欄の行(出走取消・除外など)は除外
    df = df.dropna(subset=["タイム"])
    df = df[df["タイム"].astype(str).str.strip() != ""]

    # 距離列を (馬場種別, 距離数値) に分離
    if "距離" in df.columns:
        parsed = df["距離"].apply(parse_distance_column)
        df["馬場種別"] = parsed.apply(lambda x: x[0])
        df["距離_m"] = parsed.apply(lambda x: x[1])

    df["horse_id"] = horse_id
    df["馬名"] = horse_name
    return df


def prepare_for_compare(df: pd.DataFrame) -> pd.DataFrame:
    """
    get_multiple_horses() の生データを、compare_times.py の
    load_data() が返す形式(馬名,距離,馬場状態,タイム,タイム_秒)に変換する。
    これを使えば compare_by_distance() などにそのまま渡せる。
    """
    # 元の「距離」列(例: "芝2500" のような生の文字列)を先に削除してから
    # 「距離_m」を「距離」にリネームする。順番を逆にすると同名列が2つでき、
    # astype(int) が生の文字列側にも適用されてエラーになるため注意。
    out = (
        df.drop(columns=["距離"], errors="ignore")
        .rename(columns={"距離_m": "距離", "馬場": "馬場状態"})
        .copy()
    )

    required = ["馬名", "距離", "馬場状態", "タイム"]
    missing = [col for col in required if col not in out.columns]
    if missing:
        raise KeyError(
            f"必要な列が見つかりません: {missing}\n"
            f"実際に存在する列一覧: {list(out.columns)}\n"
            "→ この列一覧を貼ってもらえれば、リネーム対応表を修正します。"
        )

    out = out[required].dropna(subset=["距離"])
    out["距離"] = out["距離"].astype(int)

    # compare_times.py の parse_time をそのまま使ってタイム_秒を追加
    from compare_times import parse_time

    out["タイム_秒"] = out["タイム"].apply(parse_time)
    return out


def compare_race_horses(race_id: str, distance: int = None) -> pd.DataFrame:
    """
    race_id を渡すだけで、そのレースの出走馬全員の持ちタイムを比較する。

    1. get_race_entries() で出走馬一覧(horse_id, 馬名)を取得
    2. get_multiple_horses() で各馬の過去成績を取得
    3. prepare_for_compare() で比較用データに変換
    4. distance を指定すればその距離での比較表(compare_by_distance)を返す。
       指定しなければ、全過去レース分のデータをそのまま返す。

    使用例:
        df = compare_race_horses("202506050812", distance=1600)
    """
    from compare_times import compare_by_distance

    entries = get_race_entries(race_id)
    print(f"出走馬 {len(entries)}頭 を取得しました: {', '.join(entries['馬名'])}")

    raw_df = get_multiple_horses(entries["horse_id"].tolist())
    if raw_df.empty:
        raise ValueError("出走馬の過去成績が1件も取得できませんでした。")

    compare_df = prepare_for_compare(raw_df)

    if distance is not None:
        return compare_by_distance(compare_df, distance)
    return compare_df


def get_multiple_horses(horse_ids: list) -> pd.DataFrame:
    """
    複数の馬IDについて過去成績をまとめて取得する。
    リクエスト間には WAIT_SECONDS の待機を入れる。
    """
    all_dfs = []
    for i, horse_id in enumerate(horse_ids):
        print(f"[{i + 1}/{len(horse_ids)}] horse_id={horse_id} を取得中...")
        try:
            df = get_horse_past_results(horse_id)
            all_dfs.append(df)
        except Exception as e:
            print(f"  ⚠️ 取得失敗: {e}")
        time.sleep(WAIT_SECONDS)  # サーバー負荷軽減のための待機

    if not all_dfs:
        return pd.DataFrame()
    return pd.concat(all_dfs, ignore_index=True)


if __name__ == "__main__":
    # ------ 使用例 ------
    # 出走予定馬の netkeiba 馬IDリストをここに入れてください
    sample_horse_ids = [
        "2019104567",  # 例: 適当なIDに差し替えてください
    ]

    result_df = get_multiple_horses(sample_horse_ids)
    print(result_df.head())

    # 取得したデータをCSVに保存(compare_times.py の load_data() に渡せる形へ
    # 必要に応じて列名を「馬名,距離,馬場状態,タイム」に変換してください)
    result_df.to_csv("netkeiba_past_results.csv", index=False, encoding="utf-8-sig")
    print("netkeiba_past_results.csv に保存しました。")
