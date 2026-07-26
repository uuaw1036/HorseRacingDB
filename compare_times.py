# -*- coding: utf-8 -*-
"""
持ちタイム比較スクリプト
------------------------------------------------
想定データ:
  netkeibaなどから取得したCSVファイル (例: horses.csv)
  以下のようなカラムを想定しています(実際の取得データに合わせて
  列名や parse_time() の正規表現は調整してください)。

  馬名,距離,馬場状態,タイム
  サンプルホースA,1600,良,1:33.4
  サンプルホースB,1600,稍重,1:34.0
  サンプルホースC,2000,良,1:59.8

  タイム列は "分:秒.コンマ秒" 形式 (例 "1:33.4") を想定しています。
  Streamlit側では load_data() と compare_by_distance() をそのまま
  import して使えるように関数化しています。
------------------------------------------------
"""

import re
import pandas as pd


def parse_time(time_str: str) -> float:
    """
    "1:33.4" や "58.2" のような文字列を秒(float)に変換する。
    分がない場合(58.2など)にも対応。
    """
    time_str = str(time_str).strip()
    match = re.match(r"^(?:(\d+):)?(\d+(?:\.\d+)?)$", time_str)
    if not match:
        raise ValueError(f"タイムの形式が不正です: {time_str}")
    minutes = int(match.group(1)) if match.group(1) else 0
    seconds = float(match.group(2))
    return minutes * 60 + seconds


def seconds_to_time_str(seconds: float) -> str:
    """秒(float)を "1:33.4" 形式の文字列に戻す(表示用)。"""
    minutes = int(seconds // 60)
    rem = seconds - minutes * 60
    if minutes > 0:
        return f"{minutes}:{rem:04.1f}"
    return f"{rem:.1f}"


def load_data(csv_path: str) -> pd.DataFrame:
    """
    CSVを読み込み、タイムを秒に変換した列 (タイム_秒) を追加して返す。
    """
    df = pd.read_csv(csv_path)
    df["タイム_秒"] = df["タイム"].apply(parse_time)
    return df


DISPLAY_COLUMNS = [
    "horse_id",  # 表示はしないが、現在のレースの馬番・人気を突き合わせるために残す
    "順位",
    "馬番",
    "馬名",
    "人気",
    "馬場状態",
    "タイム",
    "上がり3F",
    "馬体重",
    "斤量",
    "着順",
]


def best_time_per_horse(df: pd.DataFrame) -> pd.DataFrame:
    """
    馬ごとの自己ベストタイム(持ちタイム)を「距離 × 馬場種別(芝/ダ)」別に抽出する。
    芝とダートはタイムの単純比較ができないため、同じ距離でも別集計にする。
    同じ馬・同じ距離・同じ馬場種別で複数レコードがある場合は最速タイムを採用。
    """
    df = df.copy()
    if "馬場種別" not in df.columns:
        # load_data() 経由などで馬場種別が無いデータを渡された場合のフォールバック
        df["馬場種別"] = "―"

    group_cols = ["馬名", "距離", "馬場種別"]
    if "horse_id" in df.columns:
        group_cols = ["horse_id"] + group_cols

    idx = df.groupby(group_cols)["タイム_秒"].idxmin()
    best_df = df.loc[idx].copy()
    best_df["表示タイム"] = best_df["タイム_秒"].apply(seconds_to_time_str)
    return best_df.sort_values(["馬場種別", "距離", "タイム_秒"])


def compare_by_distance(df: pd.DataFrame, distance: int, surface: str = None) -> pd.DataFrame:
    """
    指定した距離(・馬場種別)の出走馬同士で持ちタイムを比較し、速い順にランキングする。

    surface: "芝" / "ダ" / "障" を指定すると、その馬場種別だけに絞り込む。
             Noneの場合は距離が一致する全馬場種別を対象にする(通常は
             呼び出し側で芝/ダートを分けて2回呼ぶ想定)。

    戻り値の列は 順位・馬番・馬名・人気・馬場状態・タイム・上がり3F・馬体重・
    斤量・着順 の順(horse_idは突き合わせ用に残すが表示側で落とす)。
    「タイム」列はここでは自己ベスト時点のタイム(表示用文字列)。
    「馬番」「人気」はここでは自己ベストを出したレース時点のものの
    ままなので、現在のレースの値に差し替える場合は呼び出し側(app.py)で
    出馬表データ(horse_id -> 馬番・人気)をマージして上書きすること。
    """
    best_df = best_time_per_horse(df)
    subset = best_df[best_df["距離"] == distance].copy()
    if surface is not None:
        subset = subset[subset["馬場種別"] == surface]
    subset = subset.sort_values("タイム_秒").reset_index(drop=True)
    subset.insert(0, "順位", subset.index + 1)

    subset = subset.drop(columns=["タイム"], errors="ignore").rename(
        columns={"表示タイム": "タイム"}
    )

    for col in DISPLAY_COLUMNS:
        if col not in subset.columns:
            subset[col] = pd.NA

    return subset[DISPLAY_COLUMNS].reset_index(drop=True)


def compare_specific_horses(
    df: pd.DataFrame, horse_names: list, distance: int, surface: str = None
) -> pd.DataFrame:
    """
    出走予定馬のリストを渡して、その距離・馬場種別での持ちタイムだけを比較する。
    (レース前に出走メンバーだけで比べたい場合に使用)
    """
    ranking = compare_by_distance(df, distance, surface)
    return ranking[ranking["馬名"].isin(horse_names)].reset_index(drop=True)


def head_to_head_records(raw_df: pd.DataFrame, entries: pd.DataFrame, target_horse_id: str):
    """
    出走予定馬同士の対戦成績(過去に同じレースに出走したことがある場合の
    着順比較)を求める。

    raw_df: 出走予定馬それぞれの get_horse_past_results() を連結したデータ
            (scrape_netkeiba.get_horse_past_results が付与する
            "race_id_key" 列を含む)。持ちタイム比較用にすでに取得済みの
            データをそのまま再利用するだけなので、追加のスクレイピングは
            発生しない。
    entries: 今回のレースの出走予定馬一覧(horse_id・馬名を含む)。
    target_horse_id: 基準にする馬の horse_id。

    戻り値: (summary_df, detail_df) のタプル。
      summary_df: 対戦相手ごとの通算成績(○勝●敗△分)。
      detail_df:  レースごとの対戦詳細(日付・レース名・着順など)。
      対戦記録が無い場合はどちらも空のDataFrame。
    """
    empty = pd.DataFrame()
    if raw_df is None or raw_df.empty or "race_id_key" not in raw_df.columns:
        return empty, empty

    target_races = raw_df.loc[
        (raw_df["horse_id"] == target_horse_id) & raw_df["race_id_key"].notna(),
        ["race_id_key", "着順", "日付", "レース名"],
    ].rename(columns={"着順": "本馬着順"})

    if target_races.empty:
        return empty, empty

    entry_ids = entries["horse_id"].tolist()
    others = raw_df.loc[
        (raw_df["horse_id"] != target_horse_id)
        & raw_df["horse_id"].isin(entry_ids)
        & raw_df["race_id_key"].notna(),
        ["horse_id", "馬名", "race_id_key", "着順"],
    ].rename(columns={"着順": "相手着順", "馬名": "対戦相手"})

    merged = others.merge(target_races, on="race_id_key", how="inner")
    if merged.empty:
        return empty, empty

    def _to_num(x):
        try:
            return int(str(x).strip())
        except (TypeError, ValueError):
            return None

    merged["_本馬着順数"] = merged["本馬着順"].apply(_to_num)
    merged["_相手着順数"] = merged["相手着順"].apply(_to_num)

    def _result(row):
        a, b = row["_本馬着順数"], row["_相手着順数"]
        if a is None or b is None:
            return "―"
        if a < b:
            return "○"
        if a > b:
            return "●"
        return "△"

    merged["結果"] = merged.apply(_result, axis=1)

    summary_df = (
        merged.groupby("対戦相手")["結果"]
        .apply(lambda s: f"{(s == '○').sum()}勝{(s == '●').sum()}敗{(s == '△').sum()}分")
        .reset_index()
        .rename(columns={"結果": "対戦成績(本馬から見て)"})
    )

    detail_df = merged[
        ["対戦相手", "日付", "レース名", "本馬着順", "相手着順", "結果"]
    ].sort_values(["対戦相手", "日付"]).reset_index(drop=True)

    return summary_df, detail_df


if __name__ == "__main__":
    # ------ 動作確認用のサンプルデータ ------
    sample_csv = "sample_horses.csv"
    sample_data = """馬名,距離,馬場種別,馬場状態,タイム
サンプルホースA,1600,芝,良,1:33.4
サンプルホースB,1600,芝,稍重,1:34.0
サンプルホースC,1600,芝,良,1:33.9
サンプルホースA,2000,芝,良,1:59.5
サンプルホースD,2000,芝,良,1:58.8
サンプルホースA,1600,ダ,良,1:36.2
"""
    with open(sample_csv, "w", encoding="utf-8") as f:
        f.write(sample_data)

    df = load_data(sample_csv)

    print("=== 芝1600m 持ちタイムランキング ===")
    print(compare_by_distance(df, 1600, "芝").to_string(index=False))

    print("\n=== 芝2000m 持ちタイムランキング ===")
    print(compare_by_distance(df, 2000, "芝").to_string(index=False))

    print("\n=== 出走予定馬だけを比較(例: A, C / 芝1600m) ===")
    print(
        compare_specific_horses(
            df, ["サンプルホースA", "サンプルホースC"], 1600, "芝"
        ).to_string(index=False)
    )
