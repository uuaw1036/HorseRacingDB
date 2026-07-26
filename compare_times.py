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


def best_time_per_horse(df: pd.DataFrame) -> pd.DataFrame:
    """
    馬ごとの自己ベストタイム(持ちタイム)を距離別に抽出する。
    同じ馬・同じ距離で複数レコードがある場合は最速タイムを採用。
    """
    idx = df.groupby(["馬名", "距離"])["タイム_秒"].idxmin()
    best_df = df.loc[idx].copy()
    best_df["表示タイム"] = best_df["タイム_秒"].apply(seconds_to_time_str)
    return best_df.sort_values(["距離", "タイム_秒"])


def compare_by_distance(df: pd.DataFrame, distance: int) -> pd.DataFrame:
    """
    指定した距離の出走馬同士で持ちタイムを比較し、速い順にランキングする。
    """
    best_df = best_time_per_horse(df)
    subset = best_df[best_df["距離"] == distance].copy()
    subset = subset.sort_values("タイム_秒").reset_index(drop=True)
    subset.insert(0, "順位", subset.index + 1)
    return subset[["順位", "馬名", "距離", "馬場状態", "表示タイム", "タイム_秒"]]


def compare_specific_horses(df: pd.DataFrame, horse_names: list, distance: int) -> pd.DataFrame:
    """
    出走予定馬のリストを渡して、その距離での持ちタイムだけを比較する。
    (レース前に出走メンバーだけで比べたい場合に使用)
    """
    ranking = compare_by_distance(df, distance)
    return ranking[ranking["馬名"].isin(horse_names)].reset_index(drop=True)


if __name__ == "__main__":
    # ------ 動作確認用のサンプルデータ ------
    sample_csv = "sample_horses.csv"
    sample_data = """馬名,距離,馬場状態,タイム
サンプルホースA,1600,良,1:33.4
サンプルホースB,1600,稍重,1:34.0
サンプルホースC,1600,良,1:33.9
サンプルホースA,2000,良,1:59.5
サンプルホースD,2000,良,1:58.8
"""
    with open(sample_csv, "w", encoding="utf-8") as f:
        f.write(sample_data)

    df = load_data(sample_csv)

    print("=== 1600m 持ちタイムランキング ===")
    print(compare_by_distance(df, 1600).to_string(index=False))

    print("\n=== 2000m 持ちタイムランキング ===")
    print(compare_by_distance(df, 2000).to_string(index=False))

    print("\n=== 出走予定馬だけを比較(例: A, C) ===")
    print(compare_specific_horses(df, ["サンプルホースA", "サンプルホースC"], 1600).to_string(index=False))
