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

import copy
import re
import time
import requests
import pandas as pd
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

# リクエスト間の待機秒数(サーバー負荷軽減のため。短くしすぎない)
WAIT_SECONDS = 1.5


def _clean_header_cell_text(th) -> str:
    """
    <th>セルからテキストを取り出す。

    netkeibaの成績テーブルには「タイム指数」列のように、見た目には
    表示されない(style="display:none")切り替え用ドロップダウンの
    テキスト("タイム指数(通常)"などの<a>要素)がヘッダーセル内部に
    埋め込まれていることがある。BeautifulSoup/pandasのテキスト抽出は
    CSSのdisplay:noneを考慮しないため、そのまま get_text() すると
    列名にこの非表示テキストが混入し、列名の重複や意図しない列名の
    原因になる。ここでは style="display:none" が付いた要素と img だけを
    取り除いてから本来のラベルだけを取得する(可視テキストをラップして
    いるだけの<div>までまとめて消してしまわないよう、hidden指定のある
    要素だけをピンポイントで除去する)。
    """
    th_copy = copy.deepcopy(th)

    hidden_tags = [
        tag
        for tag in th_copy.find_all(True)
        if re.search(r"display\s*:\s*none", tag.get("style", ""))
    ]
    for tag in hidden_tags:
        tag.decompose()

    for tag in th_copy.find_all("img"):
        tag.decompose()

    return th_copy.get_text(strip=True).replace(" ", "").replace("　", "")


def _parse_race_result_table(table) -> pd.DataFrame:
    """
    <table>要素からthead/tbodyを直接たどって列を組み立てる。

    pandas.read_html は列名の重複解消(.1などのサフィックス付与)を
    自動で行うが、その過程やヘッダーセル内の非表示テキスト混入により
    列と値がずれることがあるため、ここでは「ヘッダーの位置」と
    「各行の位置」を素直に対応させるだけの単純な方式にしている。

    あわせて、「レース名」列のリンク先URLから race_id を抜き出し、
    "race_id_key" 列として追加する(対戦成績機能で、出走予定馬同士が
    同じレースに出走していたかどうかを判定するための一意キーとして使う)。
    """
    header_row = table.find("thead").find("tr")
    columns = [_clean_header_cell_text(th) for th in header_row.find_all("th")]
    race_name_idx = columns.index("レース名") if "レース名" in columns else None

    rows = []
    race_id_keys = []
    tbody = table.find("tbody")
    for tr in (tbody.find_all("tr") if tbody else []):
        tds = tr.find_all("td")
        # ヘッダーと列数が合わない行(まれなレイアウト崩れ)は安全のためスキップ
        if len(tds) != len(columns):
            continue
        rows.append([td.get_text(strip=True) for td in tds])

        race_id_key = None
        if race_name_idx is not None:
            a = tds[race_name_idx].find("a", href=True)
            if a is not None:
                m = re.search(r"/race/(\d+)/?", a["href"])
                if m:
                    race_id_key = m.group(1)
        race_id_keys.append(race_id_key)

    result = pd.DataFrame(rows, columns=columns)
    result["race_id_key"] = race_id_keys
    return result


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


def parse_venue_name(value: str) -> str:
    """
    以下のような開催表記から競馬場名だけを取り出す。
      - "3阪神6"          (馬柱ページの「開催」欄。N回+場名+N日目が連結)
      - "2回 福島 3日目"   (レース一覧ページの開催名。空白区切り)
    競馬場名以外の部分(開催回数・日目・空白)を取り除いて返す。
    """
    text = str(value).strip()

    # 空白区切りで "N回 場名 N日目" になっているケース(レース一覧ページ)
    tokens = text.split()
    if (
        len(tokens) == 3
        and re.fullmatch(r"\d+回", tokens[0])
        and re.fullmatch(r"\d+日目?", tokens[2])
    ):
        return tokens[1]

    # 空白なしで詰まっている "3阪神6" のようなケース(過去成績ページ)
    m = re.search(r"[^\d\s]+", text)
    return m.group(0) if m else text


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


def _extract_ninki(tr):
    """
    <tr>から「人気」の値を取得する。

    netkeibaのページには、人気の持たせ方が少なくとも3パターンある。

    1. 中央競馬の出馬表(shutuba.html)のパターン:
       人気のセルには "Popular_Ninki" という専用クラスが付いており、
       中の<span>にそのまま数字が入っている。
         <td class="Popular Popular_Ninki Txt_C"><span id="ninki-1_17">11</span></td>
       オッズのセルは同じ "Popular" クラスを持つが "Popular_Ninki" は付かない
       (例: <td class="Txt_R Popular"><span ...>34.4</span></td>)ため、
       "Popular_Ninki" の有無で確実に区別できる。

    2. 中央競馬の結果ページ(race.netkeiba.com、レース確定後)のパターン:
       クラス名が "Popular" ではなく "Odds" になっており、人気の値は
       専用の <span class="OddsPeople">1</span> に入っている。
         - 人気:  <td class="Odds BgYellow Txt_C"><span class="OddsPeople">1</span></td>
         - オッズ: <td class="Odds Txt_R"><span class="Odds_Ninki">4.8</span></td>

    3. 地方競馬の出馬表(nar.netkeiba.com/race/shutuba.html)のパターン:
       中央競馬と違い "Popular_Ninki" のような専用クラスは付かず、
       "Popular" クラスだけがオッズ・人気の両方のセルに共通して付く。
       ただし、揃え方(Txt_R/Txt_C)で区別できる。
         - オッズ: <td class="Popular Txt_R">110.2</td>
         - 人気:   <td class="Popular Txt_C">7</td>
       ("Popular" と "Txt_C" を両方持つセル)
       中身は<span>で包まれておらず、tdのテキストにそのまま数字が入っている。

    "OddsPeople" と "Popular_Ninki" はどちらも人気専用の目印で他の値と
    紛れないため、まず "OddsPeople" を最優先で探し、次に "Popular_Ninki"、
    最後に地方競馬パターン("Popular"+"Txt_C")を探す。
    """
    people_span = tr.find("span", class_="OddsPeople")
    if people_span is not None:
        text = people_span.get_text(strip=True)
        m = re.search(r"\d+", text)
        if m:
            return int(m.group())

    for td in tr.find_all("td"):
        classes = td.get("class") or []
        if "Popular_Ninki" not in classes:
            continue
        text = td.get_text(strip=True)
        m = re.search(r"\d+", text)
        if m:
            return int(m.group())

    # パターン3: 地方競馬(NAR)。"Popular"かつ"Txt_C"のtdが人気、
    # "Popular"かつ"Txt_R"のtdがオッズなので、Txt_Cの方だけを拾う。
    for td in tr.find_all("td"):
        classes = td.get("class") or []
        if "Popular" in classes and "Txt_C" in classes:
            text = td.get_text(strip=True)
            m = re.fullmatch(r"\d+", text)
            if m:
                return int(m.group())

    return None


def _extract_umaban(tr):
    """
    <tr>から「馬番」の値を取得する。

    netkeibaのページには、馬番の持たせ方が少なくとも2パターンある。

    1. 出馬表(発走前)のパターン:
         <td class="Umaban1">1</td>
       のように、馬番の数字を含んだclass名が振られている。

    2. 中央競馬の結果ページ(発走後)のパターン:
         枠番: <td class="Num Waku3"><div>3</div></td>
         馬番: <td class="Num Txt_C"><div>3</div></td>
       "Num"クラスは枠番・馬番どちらのtdにも付き、馬番の数字はclass名では
       なくtdの中身(text)に入っている。枠番側だけ class名に "WakuN" が
       含まれるので、それを手がかりに枠番側を除外して馬番側を判定する。
    """
    # パターン1: 出馬表(発走前)
    for td in tr.find_all("td"):
        classes = td.get("class") or []
        if any(c.startswith("Umaban") for c in classes):
            text = td.get_text(strip=True)
            if text.isdigit():
                return int(text)

    # パターン2: 結果ページ(発走後)
    for td in tr.find_all("td"):
        classes = td.get("class") or []
        if "Num" not in classes:
            continue
        if any(c.startswith("Waku") for c in classes):
            continue  # 枠番側のtdはスキップ
        text = td.get_text(strip=True)
        if text.isdigit():
            return int(text)

    return None


def get_win_odds_ninki(race_id: str, central: bool = True) -> pd.DataFrame:
    """
    単勝オッズ・人気を専用APIから取得する(馬番, 単勝オッズ, 人気)。

    出馬表ページ(shutuba.html)の人気・オッズ欄は、静的HTMLには
    空のプレースホルダー("**"など)しか入っておらず、ブラウザ上で
    JavaScriptがこのAPIを叩いてDOMに書き込んで表示している。そのため
    requestsで静的HTMLを取得しただけでは値が取れない。このAPIを
    直接叩くことで、JSを実行せずに実際のオッズ・人気を取得できる。

    URL: https://race.netkeiba.com/api/api_get_jra_odds.html
         ?race_id={race_id}&type=1&action=init
      (type=1 が単勝オッズ。動作確認済み。)

    レスポンス例:
      {"status":"yoso","data":{"odds":{"1":{"12":["1.9","","1"], ...}}}, ...}
      外側の"1"は券種(1=単勝)固定。内側のキーが馬番(文字列)、値が
      [オッズ, (未使用/複勝オッズ枠?), 人気] の3要素配列になっている。

    central: True なら中央競馬(race.netkeiba.com)。この関数は中央競馬専用で、
             地方競馬(nar.netkeiba.com)はURL・レスポンス形式が異なるため
             get_win_odds_ninki_nar() を使うこと。Falseの場合は呼び出さず
             空のDataFrameを返す。
    ※ オッズ発表前・レース確定後などでデータが無い場合は
      status が "NG" になるので、その場合も空のDataFrameを返す
      (エラーにはしない。呼び出し側は静的HTML側の値をそのまま使えばよい)。
    """
    empty = pd.DataFrame(columns=["馬番", "単勝オッズ", "人気"])
    if not central:
        return empty

    url = (
        "https://race.netkeiba.com/api/api_get_jra_odds.html"
        f"?race_id={race_id}&type=1&action=init"
    )
    headers = dict(HEADERS)
    headers["Referer"] = f"https://race.netkeiba.com/race/shutuba.html?race_id={race_id}"
    headers["X-Requested-With"] = "XMLHttpRequest"

    response = requests.get(url, headers=headers, timeout=10)
    response.raise_for_status()

    try:
        payload = response.json()
    except ValueError:
        return empty

    odds_by_umaban = payload.get("data", {}).get("odds", {}).get("1", {})
    if not odds_by_umaban:
        return empty

    records = []
    for umaban_str, values in odds_by_umaban.items():
        if not str(umaban_str).isdigit():
            continue
        odds_str = values[0] if len(values) > 0 else ""
        ninki_str = values[2] if len(values) > 2 else ""
        try:
            odds = float(odds_str) if odds_str else None
        except ValueError:
            odds = None
        try:
            ninki = int(ninki_str) if ninki_str else None
        except ValueError:
            ninki = None
        records.append({"馬番": int(umaban_str), "単勝オッズ": odds, "人気": ninki})

    if not records:
        return empty
    return pd.DataFrame(records).sort_values("馬番").reset_index(drop=True)


def get_win_odds_ninki_nar(race_id: str) -> pd.DataFrame:
    """
    地方競馬(NAR)の単勝オッズ・人気を専用APIから取得する(馬番, 単勝オッズ, 人気)。

    中央競馬(get_win_odds_ninki)とはURL・レスポンス形式が異なるため
    専用の関数にしている。地方競馬の出馬表は静的HTMLの時点で既に
    人気(td class="Popular Txt_C")が入っていることが多く、このAPIは
    その値がまだ入っていない場合(レース直前でオッズが未確定など)の
    フォールバックとして使う。

    URL: https://nar.netkeiba.com/api/api_get_nar_odds.html
         ?race_id={race_id}&type=1&action=init
      (type=1 が単勝オッズ。動作確認済み。)

    レスポンス例:
      {"status":"OK","odds_status":"real",
       "ary_odds":{"01":{"Odds":"110.2","Ninki":7}, "02":{"Odds":"7.8","Ninki":4}, ...}}
      "ary_odds"のキーが馬番(2桁ゼロ埋め文字列)、値が
      {"Odds": オッズ文字列, "Ninki": 人気(int)} の辞書になっている。

    ※ オッズ発表前・レース確定後などでデータが無い場合はstatusが
      "NG"になる(中央競馬側と同様)。その場合も空のDataFrameを返す。
    """
    empty = pd.DataFrame(columns=["馬番", "単勝オッズ", "人気"])

    url = (
        "https://nar.netkeiba.com/api/api_get_nar_odds.html"
        f"?race_id={race_id}&type=1&action=init"
    )
    headers = dict(HEADERS)
    headers["Referer"] = f"https://nar.netkeiba.com/race/shutuba.html?race_id={race_id}"
    headers["X-Requested-With"] = "XMLHttpRequest"

    response = requests.get(url, headers=headers, timeout=10)
    response.raise_for_status()

    try:
        payload = response.json()
    except ValueError:
        return empty

    if payload.get("status") != "OK":
        return empty

    ary_odds = payload.get("ary_odds", {})
    if not ary_odds:
        return empty

    records = []
    for umaban_str, values in ary_odds.items():
        if not str(umaban_str).isdigit():
            continue
        odds_str = values.get("Odds", "") if isinstance(values, dict) else ""
        ninki_val = values.get("Ninki") if isinstance(values, dict) else None
        try:
            odds = float(odds_str) if odds_str else None
        except ValueError:
            odds = None
        try:
            ninki = int(ninki_val) if ninki_val is not None else None
        except (ValueError, TypeError):
            ninki = None
        # "01"のようなゼロ埋め文字列でもintに変換すれば正しい馬番になる
        records.append({"馬番": int(umaban_str), "単勝オッズ": odds, "人気": ninki})

    if not records:
        return empty
    return pd.DataFrame(records).sort_values("馬番").reset_index(drop=True)


def get_race_entries(race_id: str, central: bool = True) -> pd.DataFrame:
    """
    レースの出馬表(発走前)または結果(発走後にこのページを見た場合)の
    ページから、出走馬の horse_id・馬名・馬番・人気の一覧を取得する。

    central: True なら中央競馬(race.netkeiba.com)、False なら地方競馬
             (nar.netkeiba.com)のページを見にいく。馬個別の過去成績
             (get_horse_past_results)はどちらの場合も db.netkeiba.com を
             見るため、この引数の影響を受けない(同じ関数がそのまま使える)。

    URL: https://{race.netkeiba.com か nar.netkeiba.com}/race/shutuba.html?race_id={race_id}
    race_id の調べ方: netkeibaでレースページを開き、URLの "race_id=" の
    後ろの数字部分をコピーする(例: race_id=202506050812)。

    ※ このページは発走前は出馬表、発走後は結果(着順・タイム・人気など)が
      そのまま表示されることがあり、CSSクラス名も微妙に異なる。まずは
      出走馬1頭ごとに振られる <tr class="HorseList"> という行クラス
      (中央・地方どちらのshutuba.htmlでも共通)を頼りに抽出し、それが
      1件も見つからない場合だけ、テーブルの見出し(<thead>)から
      「馬番」列の位置を判定する方式にフォールバックする。
    """
    domain = "race.netkeiba.com" if central else "nar.netkeiba.com"
    url = f"https://{domain}/race/shutuba.html?race_id={race_id}"
    response = requests.get(url, headers=HEADERS)
    response.raise_for_status()
    response.encoding = "utf-8"

    soup = BeautifulSoup(response.text, "lxml")

    seen = {}

    # --- 1) <tr class="HorseList"> 行から直接抽出する(最優先) ---------
    # ヘッダー(<thead>)の文言やテーブル構成に左右されないため、
    # サイト側の見出しラベルが変わっても壊れにくい。
    horse_list_rows = [
        tr for tr in soup.find_all("tr") if "HorseList" in (tr.get("class") or [])
    ]
    for tr in horse_list_rows:
        a = tr.find("a", href=re.compile(r"/horse/\d{8,12}"))
        if a is None:
            continue
        m = re.search(r"/horse/(\d{8,12})/?(?:[\"?]|$)", a["href"])
        if not m:
            continue
        horse_id = m.group(1)
        if horse_id in seen:
            continue
        name = a.get("title") or a.text.strip()
        if not name:
            continue

        seen[horse_id] = {
            "馬名": name,
            "馬番": _extract_umaban(tr),
            "人気": _extract_ninki(tr),
        }

    # --- 2) 上でうまく取れなかった場合、「馬番」列を持つテーブルを探し、 ---
    # そのヘッダーから列位置を判定する方式にフォールバックする。
    if not seen:
        target_table = None
        header_texts = []
        for t in soup.find_all("table"):
            thead = t.find("thead")
            if thead is None or thead.find("tr") is None:
                continue
            texts = [_clean_header_cell_text(th) for th in thead.find("tr").find_all("th")]
            if "馬番" in texts:
                target_table = t
                header_texts = texts
                break

        umaban_idx = header_texts.index("馬番") if "馬番" in header_texts else None

        tbody = target_table.find("tbody") if target_table is not None else None
        for tr in (tbody.find_all("tr") if tbody is not None else []):
            a = tr.find("a", href=re.compile(r"/horse/\d{8,12}"))
            if a is None:
                continue
            m = re.search(r"/horse/(\d{8,12})/?(?:[\"?]|$)", a["href"])
            if not m:
                continue
            horse_id = m.group(1)
            if horse_id in seen:
                continue
            name = a.get("title") or a.text.strip()
            if not name:
                continue

            tds = tr.find_all("td")
            umaban = None
            if umaban_idx is not None and umaban_idx < len(tds):
                text = tds[umaban_idx].get_text(strip=True)
                if text.isdigit():
                    umaban = int(text)

            ninki = _extract_ninki(tr)

            seen[horse_id] = {"馬名": name, "馬番": umaban, "人気": ninki}

    # --- テーブルからの構造化抽出がうまくいかなかった場合のフォールバック ---
    # (ページ構造が想定と大きく異なる場合の保険。クラス名頼みの旧方式)
    if not seen:
        for a in soup.find_all("a", href=True):
            match = re.search(r"/horse/(\d{8,12})/?(?:[\"?]|$)", a["href"])
            if not match:
                continue
            horse_id = match.group(1)
            if horse_id in seen:
                continue
            name = a.get("title") or a.text.strip()
            if not name:
                continue

            umaban = None
            ninki = None
            tr = a.find_parent("tr")
            if tr is not None:
                umaban_cell = tr.select_one("td[class*='Umaban']")
                if umaban_cell is None:
                    for td in tr.find_all("td"):
                        text = td.get_text(strip=True)
                        if text.isdigit():
                            umaban_cell = td
                            break
                if umaban_cell is not None:
                    text = umaban_cell.get_text(strip=True)
                    if text.isdigit():
                        umaban = int(text)

                ninki = _extract_ninki(tr)

            seen[horse_id] = {"馬名": name, "馬番": umaban, "人気": ninki}

    if not seen:
        raise ValueError(
            f"出走馬が見つかりませんでした (race_id={race_id})。"
            "race_idが正しいか、レースがまだ発表されていない可能性があります。"
        )

    df = pd.DataFrame(
        [
            {"horse_id": hid, "馬名": v["馬名"], "馬番": v["馬番"], "人気": v["人気"]}
            for hid, v in seen.items()
        ]
    )

    # 静的HTMLには人気が入っていない場合(中央競馬はJSで後から埋め込む
    # ため全行が空欄になりやすい。地方競馬は静的HTMLに人気があることが
    # 多いが、レース直前などでまだ確定していないと同様に空欄になりうる)、
    # そのときは専用オッズAPIから取り直して埋める。
    # 馬番は静的HTML側で取得できている前提(馬番自体はJS埋め込みではない)。
    if df["人気"].isna().all():
        try:
            odds_df = (
                get_win_odds_ninki(race_id, central=True)
                if central
                else get_win_odds_ninki_nar(race_id)
            )
        except requests.RequestException:
            odds_df = pd.DataFrame(columns=["馬番", "単勝オッズ", "人気"])

        if not odds_df.empty:
            # 型が違うとmerge時にValueErrorになることがあるため、
            # 両方とも null許容の Int64 に揃えてからマージする。
            df = df.copy()
            df["馬番"] = pd.array(pd.to_numeric(df["馬番"], errors="coerce"), dtype="Int64")
            odds_df = odds_df.copy()
            odds_df["馬番"] = pd.array(
                pd.to_numeric(odds_df["馬番"], errors="coerce"), dtype="Int64"
            )
            df = df.drop(columns=["人気"]).merge(
                odds_df[["馬番", "人気"]], on="馬番", how="left"
            )

    return df


def get_race_list(date: str, central: bool = True) -> pd.DataFrame:
    """
    指定した開催日に行われるレースの一覧(race_id・開催場・R番号・
    発走時刻・レース名)を取得する。

    date: "YYYYMMDD" 形式の文字列 (例: "20250504")。"2025-05-04" や
          "2025/05/04" のようにハイフン・スラッシュ入りで渡しても
          自動的に取り除いて解釈する。
    central: True なら中央競馬(race.netkeiba.com)、False なら地方競馬
             (nar.netkeiba.com)の開催日程を見にいく。

    URL: https://{race.netkeiba.com か nar.netkeiba.com}/top/race_list_sub.html?kaisai_date={date}

    ※ 「race_list.html」(末尾に "_sub" が付かない方)はJavaScriptで
      レース一覧を後から描画するページで、requestsで取得した生HTMLには
      実データが含まれていない(確認済み)。実データは race_list.js が
      Ajaxで読み込んでいる「race_list_sub.html」側に載っているため、
      必ずこちらを使うこと。
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

    url = f"https://{'race' if central else 'nar'}.netkeiba.com/top/race_list_sub.html?kaisai_date={date}"
    sub_headers = dict(HEADERS)
    sub_headers["Referer"] = (
        f"https://{'race' if central else 'nar'}.netkeiba.com/top/race_list.html?kaisai_date={date}"
    )
    sub_headers["X-Requested-With"] = "XMLHttpRequest"
    response = requests.get(url, headers=sub_headers)
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

    # pandas.read_html は使わず、thead/tbodyを直接たどって列を組み立てる
    # (ヘッダーセル内の非表示ドロップダウンテキストの混入や、列名重複の
    # 自動解消処理によって値がずれる問題を避けるため)。
    #
    # 「タイム」列を持つテーブルは複数存在することがある(例: ページ上部の
    # 簡易な近走サマリー表など)。それらは列数が少なく「上り」(上がり3F)や
    # 「馬体重」を含まないことが多いため、単純に最初に見つかったものを
    # 使うと上がり3F・馬体重が空になってしまう。
    # そこで、まず「タイム」に加えて「上り」「馬体重」も含む(=本来の
    # 全成績テーブルらしい)ものを優先し、それが無ければ列数が最も多い
    # テーブルを採用する。
    candidates = []
    for t in soup.find_all("table"):
        thead = t.find("thead")
        if thead is None or thead.find("tr") is None:
            continue
        header_texts = [_clean_header_cell_text(th) for th in thead.find("tr").find_all("th")]
        if "タイム" in header_texts:
            candidates.append((t, header_texts))

    df = None
    for t, header_texts in candidates:
        if "上り" in header_texts and "馬体重" in header_texts:
            df = _parse_race_result_table(t)
            break
    if df is None and candidates:
        best_t, _ = max(candidates, key=lambda pair: len(pair[1]))
        df = _parse_race_result_table(best_t)

    if df is None:
        raise ValueError(
            f"過去成績テーブルが見つかりませんでした (horse_id={horse_id})。"
            "新馬(未出走)の可能性、またはサイトのHTML構造が変わっている可能性があります。"
        )

    # タイムが空欄の行(出走取消・除外など)は除外
    df = df[df["タイム"].astype(str).str.strip() != ""]

    # 距離列を (馬場種別, 距離数値) に分離
    if "距離" in df.columns:
        parsed = df["距離"].apply(parse_distance_column)
        df["馬場種別"] = parsed.apply(lambda x: x[0])
        df["距離_m"] = parsed.apply(lambda x: x[1])

    # 開催列(例:"3阪神6" = 3回阪神6日目)から競馬場名だけを取り出して「場」列にする
    if "開催" in df.columns:
        df["場"] = df["開催"].apply(parse_venue_name)

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
        .rename(columns={"距離_m": "距離", "馬場": "馬場状態", "上り": "上がり3F"})
        .copy()
    )

    # 馬場種別(芝/ダ)は距離と同じ距離数値でも別物として扱うために必須にする
    required = ["馬名", "距離", "馬場状態", "タイム", "馬場種別"]
    missing = [col for col in required if col not in out.columns]
    if missing:
        raise KeyError(
            f"必要な列が見つかりません: {missing}\n"
            f"実際に存在する列一覧: {list(out.columns)}\n"
            "→ この列一覧を貼ってもらえれば、リネーム対応表を修正します。"
        )

    # horse_id は、このあと現在のレースの「出馬表の馬番」を突き合わせる
    # (=過去成績の馬番はそのレース時点のものなので使わない)ために残しておく。
    if "horse_id" not in out.columns:
        out["horse_id"] = pd.NA

    # 人気・斤量・上がり3F・馬体重・着順・場・通過はあれば引き継ぐ。無い場合は
    # 空欄(NaN)の列として追加し、呼び出し側(compare_times.py)が常に同じ
    # 列構成を前提にできるようにする。
    optional = ["人気", "斤量", "上がり3F", "馬体重", "着順", "場", "通過"]
    for col in optional:
        if col not in out.columns:
            out[col] = pd.NA

    out = out[["horse_id"] + required + optional].dropna(subset=["距離"])
    out["距離"] = out["距離"].astype(int)

    # compare_times.py の parse_time をそのまま使ってタイム_秒を追加
    from compare_times import parse_time

    out["タイム_秒"] = out["タイム"].apply(parse_time)
    return out


def compare_race_horses(race_id: str, distance: int = None, central: bool = True) -> pd.DataFrame:
    """
    race_id を渡すだけで、そのレースの出走馬全員の持ちタイムを比較する。

    1. get_race_entries() で出走馬一覧(horse_id, 馬名)を取得
    2. get_multiple_horses() で各馬の過去成績を取得
    3. prepare_for_compare() で比較用データに変換
    4. distance を指定すればその距離での比較表(compare_by_distance)を返す。
       指定しなければ、全過去レース分のデータをそのまま返す。

    central: True なら中央競馬、False なら地方競馬のrace_idとして扱う
             (get_race_entries に渡すだけで、各馬の過去成績取得自体は
             どちらでも db.netkeiba.com を見るため変わらない)。

    使用例:
        df = compare_race_horses("202506050812", distance=1600)
    """
    from compare_times import compare_by_distance

    entries = get_race_entries(race_id, central=central)
    print(f"出走馬 {len(entries)}頭 を取得しました: {', '.join(entries['馬名'])}")

    raw_df = get_multiple_horses(entries["horse_id"].tolist())
    if raw_df.empty:
        raise ValueError("出走馬の過去成績が1件も取得できませんでした。")

    compare_df = prepare_for_compare(raw_df)

    if distance is not None:
        return compare_by_distance(compare_df, distance)
    return compare_df


JIRO8_BASE_URL = "https://jiro8.sakura.ne.jp/index.php"

# jiro8のページでは「N走前の成績」の開始行から数えて、必ず13行後(offset +13)が
# 「スピード指数」の行になる(先行指数→ペース指数→上がり指数→スピード指数の
# 4行が各ブロックの末尾に固定順で並ぶため)。２走前以降のブロックではラベル
# テキストが省略される(先頭の「前走の成績」ブロックにしか付かない)ため、
# ラベル文字列ではなく行位置(オフセット)で判定する。
JIRO8_SPEED_INDEX_OFFSET = 13
JIRO8_BLOCK_START_LABELS = {
    "前走の成績",
    "２走前の成績",
    "３走前の成績",
    "４走前の成績",
    "５走前の成績",
}


def race_id_to_jiro8_code(race_id: str) -> str:
    """
    netkeibaのrace_idからjiro8サイトの code パラメータを組み立てる。
    例: race_id "202609030611" → code "2609030611"
        (先頭2桁の"20"(西暦の上2桁)を取り除いたものがcode)
    """
    race_id = str(race_id).strip()
    if race_id.startswith("20") and len(race_id) > 2:
        return race_id[2:]
    return race_id


def _parse_jiro8_speed_index(html: str) -> pd.DataFrame:
    """
    jiro8のレースページHTMLから、馬番ごとの過去5走分の「スピード指数」を
    抽出し、平均指数・最高指数を計算して返す(列: 馬番, 平均指数, 最高指数)。

    ページ構造がHTML/コード解析目的で組まれておらず(pandas.read_html等では
    崩れやすい)、かつ「スピード指数」のラベルは１走前のブロックにしか
    付いていないため、行位置(オフセット)で判定する専用ロジックにしている。
    """
    soup = BeautifulSoup(html, "lxml")

    # 「馬番」行を探し、そのtdの並び(末尾のラベルセルを除く)から
    # 列位置→馬番の対応を作る。この行の親<tbody>が出走馬データ表全体。
    umaban_row = None
    data_tbody = None
    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        if tds and tds[-1].get_text(strip=True) == "馬番":
            umaban_row = tds[:-1]
            # jiro8が返す生HTMLには <tbody> タグが無いことがある
            # (ブラウザ側でDOM表示する際に自動補完されるだけで、
            # requestsで取得した生のレスポンスには含まれない場合がある)。
            # find_parent("tbody") だとその場合に None になってしまうため、
            # tbodyの有無に関わらず直接の親要素を使う。
            data_tbody = tr.parent
            break

    if umaban_row is None or data_tbody is None:
        title_tag = soup.find("title")
        title_text = title_tag.get_text(strip=True) if title_tag else "(titleタグなし)"
        full_text = soup.get_text()
        umaban_anywhere = "馬番" in full_text
        snippet = re.sub(r"\s+", " ", html).strip()[:300]
        raise ValueError(
            "jiro8ページから「馬番」行が見つかりませんでした。"
            f"ページタイトル: {title_text!r} / "
            f"ページ内のどこかに「馬番」という文字列があるか: {umaban_anywhere}\n"
            "「馬番」がどこにも無い場合は、そのcode(race_id)のレースが"
            "jiro8にまだ掲載されていない(レース直前まで掲載されないことが多い)か、"
            "サイト側でエラーページ・アクセス制限ページが返っている可能性が高いです。"
            "ある場合はテーブルの行構成自体が変わった可能性があります。"
            f"\n取得できた内容の先頭: {snippet!r}"
        )

    umaban_list = []
    for td in umaban_row:
        text = td.get_text(strip=True)
        umaban_list.append(int(text) if text.isdigit() else None)
    n_cols = len(umaban_list)

    rows = data_tbody.find_all("tr", recursive=False)

    speed_rows = []
    for i, tr in enumerate(rows):
        tds = tr.find_all("td")
        if not tds:
            continue
        label = tds[-1].get_text(strip=True)
        if label in JIRO8_BLOCK_START_LABELS and i + JIRO8_SPEED_INDEX_OFFSET < len(rows):
            speed_tds = rows[i + JIRO8_SPEED_INDEX_OFFSET].find_all("td")
            if len(speed_tds) - 1 == n_cols:  # 末尾はラベルセルなので-1
                speed_rows.append(speed_tds[:-1])

    per_horse = {umaban: [] for umaban in umaban_list if umaban is not None}
    for row in speed_rows:
        for umaban, td in zip(umaban_list, row):
            if umaban is None:
                continue
            text = td.get_text(strip=True)
            try:
                value = float(text)
            except ValueError:
                continue  # 出走取消・除外などで指数が空欄の場合はスキップ
            per_horse[umaban].append(value)

    records = []
    for umaban, values in per_horse.items():
        if not values:
            continue
        records.append(
            {
                "馬番": umaban,
                "平均指数": round(sum(values) / len(values), 1),
                "最高指数": max(values),
            }
        )
    return pd.DataFrame(records)


# jiro8はHTTPヘッダー/meta共にcharset指定が曖昧なため、requestsの
# apparent_encoding(chardetによる自動判定)がShift_JIS系のページを
# 誤ってEUC-JPなどと判定し、「馬番」などの文字列が文字化けして
# 一致しなくなることがある。判定に頼らず、実際に「馬番」という文字列が
# 正しくデコードできる候補を順番に試す。
JIRO8_ENCODING_CANDIDATES = ["cp932", "shift_jis", "euc-jp", "utf-8"]


def _decode_jiro8_response(response) -> str:
    """
    jiro8のレスポンスを、候補の文字コードを順に試してデコードする。
    「馬番」という文字列が実際に含まれている結果を採用する。
    どれを試しても見つからない場合は、最初にデコードに成功したものを
    (文字化けのままでも)返す。呼び出し元でエラーメッセージを出す。
    """
    raw = response.content
    fallback_text = None
    for enc in JIRO8_ENCODING_CANDIDATES:
        try:
            text = raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
        if fallback_text is None:
            fallback_text = text
        if "馬番" in text:
            return text
    return fallback_text if fallback_text is not None else response.text


def get_speed_index_by_umaban(race_id: str) -> pd.DataFrame:
    """
    jiro8サイトから対象レースの各馬(馬番)の過去5走分の「スピード指数」を
    取得し、馬番ごとの平均指数・最高指数を返す(列: 馬番, 平均指数, 最高指数)。

    race_id: netkeibaのレースID(例: "202609030611")。先頭の"20"を除いた
             ものがjiro8のURLパラメータ code になる。

    ※ jiro8は中央競馬のレースしか扱っていないサイトのため、地方競馬の
      race_id を渡すと対応ページが存在せず「馬番」行が見つからずに
      失敗する。中央競馬かどうかの判定は呼び出し側(app.py)で行い、
      地方競馬の場合はそもそもこの関数を呼ばないようにすること。
    ※ jiro8はRefererやセッション(Cookie)の有無を見ているらしく、
      トップページを経由せずに index.php?code=... へ直接アクセスすると、
      本来のレースページではなく汎用ページ(トップページ相当)が返って
      くることがある。そのため、まずトップページに一度アクセスして
      Cookieを取得し、Refererを付けたうえで本来のページを取得する。
    ※ それでも失敗する場合は、jiro8側にまだそのレースのデータが
      掲載されていない(レース直前まで掲載されないことが多い)可能性が高い。
    """
    code = race_id_to_jiro8_code(race_id)
    url = f"{JIRO8_BASE_URL}?code={code}"

    session = requests.Session()
    # まずトップページにアクセスしてセッションCookieを確立する
    # (ここが失敗しても、本アクセス自体はダメ元で試みる)
    try:
        session.get(JIRO8_BASE_URL, headers=HEADERS, timeout=10)
        time.sleep(0.5)
    except requests.RequestException:
        pass

    sub_headers = dict(HEADERS)
    sub_headers["Referer"] = JIRO8_BASE_URL
    response = session.get(url, headers=sub_headers)
    response.raise_for_status()
    html = _decode_jiro8_response(response)

    try:
        return _parse_jiro8_speed_index(html)
    except ValueError as e:
        raise ValueError(
            f"{e}\n"
            f"URL: {url}\n"
            "※ トップページ経由(Referer・Cookieあり)でアクセスしても改善"
            "しない場合、jiro8側にまだこのレースのデータが掲載されていない"
            "可能性があります。レース直前に改めて実行してみてください。"
        ) from e


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
