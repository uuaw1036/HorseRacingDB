import time as _time
from datetime import date

import pandas as pd
import streamlit as st

from scrape_netkeiba import (
    WAIT_SECONDS,
    get_horse_past_results,
    get_race_entries,
    get_race_list,
    prepare_for_compare,
)
from compare_times import best_time_per_horse, compare_by_distance

st.set_page_config(page_title="持ちタイム比較", page_icon="🏇", layout="centered")

st.title("🏇 持ちタイム比較ダッシュボード")
st.write(
    "これから行われるレースの出走馬について、過去の持ちタイム（自己ベスト）"
    "を自動取得して比較します。"
)

AUTO_LABEL = "全体（距離ごとの自己ベスト一覧）"

# --- セッション状態の初期化 -------------------------------------------
# race_list_df: 選択した開催日のレース候補一覧
# compare_df / compare_entries / compare_race_id: 一度取得した出走馬の
#   持ちタイムデータ。距離の切り替えはここから再計算するだけにして、
#   スクレイピングを何度も走らせないようにする。
for key in ["race_list_df", "compare_df", "compare_entries", "compare_race_id"]:
    if key not in st.session_state:
        st.session_state[key] = None


def fetch_compare_data(race_id: str):
    """race_id の出走馬を取得し、持ちタイム比較用データを1回だけ取得してセッションに保存する。"""
    with st.spinner("出走馬一覧を取得中..."):
        entries = get_race_entries(race_id)

    st.success(f"出走馬 {len(entries)}頭 を取得しました: {', '.join(entries['馬名'])}")

    progress_bar = st.progress(0.0)
    status_text = st.empty()

    all_dfs = []
    horse_ids = entries["horse_id"].tolist()
    names = entries["馬名"].tolist()

    for i, (hid, name) in enumerate(zip(horse_ids, names)):
        status_text.text(f"過去成績を取得中... {name} ({i + 1}/{len(horse_ids)})")
        try:
            df = get_horse_past_results(hid)
            all_dfs.append(df)
        except Exception as e:
            st.warning(f"⚠️ {name} の取得に失敗しました: {e}")
        progress_bar.progress((i + 1) / len(horse_ids))
        if i < len(horse_ids) - 1:
            _time.sleep(WAIT_SECONDS)

    status_text.text("取得完了")

    if not all_dfs:
        st.error("出走馬の過去成績が1件も取得できませんでした。")
        return

    raw_df = pd.concat(all_dfs, ignore_index=True)
    compare_df = prepare_for_compare(raw_df)

    st.session_state.compare_df = compare_df
    st.session_state.compare_entries = entries
    st.session_state.compare_race_id = race_id


def render_result_table(compare_df: pd.DataFrame, distance_choice):
    """セッションに保存済みのデータから、選択された距離のテーブルだけを表示する(再取得なし)。"""
    if distance_choice == AUTO_LABEL:
        result = best_time_per_horse(compare_df)
        cols = ["馬名"]
        if "馬番" in result.columns:
            cols.append("馬番")
        cols += ["距離", "馬場状態"]
        for c in ["人気", "斤量"]:
            if c in result.columns:
                cols.append(c)
        cols.append("表示タイム")
        result = result[cols].reset_index(drop=True)
        if result.empty:
            st.info("比較できる過去成績が見つかりませんでした。")
        else:
            st.success("分析完了！（距離ごとの自己ベスト一覧）")
            st.dataframe(result, use_container_width=True)
    else:
        result = compare_by_distance(compare_df, distance_choice)
        if result.empty:
            st.info(f"出走馬の中に {distance_choice}mを走った記録がある馬はいません。")
        else:
            st.success(f"分析完了！（{distance_choice}m 持ちタイムランキング）")
            st.dataframe(result, use_container_width=True)


# --- 1. 開催日からレースを選ぶ -----------------------------------------
selected_date = st.date_input("開催日を選択", value=date.today())

if st.button("この日のレースを検索", use_container_width=True, key="search_races"):
    date_str = selected_date.strftime("%Y%m%d")
    try:
        with st.spinner("レース一覧を取得中..."):
            st.session_state.race_list_df = get_race_list(date_str)
    except Exception as e:
        st.session_state.race_list_df = None
        st.error(f"レース一覧の取得に失敗しました: {e}")

races_df = st.session_state.race_list_df

if races_df is not None and races_df.empty:
    st.info("この日のレースは見つかりませんでした。")

elif races_df is not None:

    def _label(row) -> str:
        parts = [
            p
            for p in [
                row["発走時刻"],
                row["開催"],
                (row["R"] if row["R"] else ""),
                row["レース名"],
                (f"({row['距離']})" if row.get("距離") else ""),
            ]
            if p
        ]
        return " / ".join(parts) if parts else row["race_id"]

    races_df = races_df.copy()
    races_df["表示名"] = races_df.apply(_label, axis=1)

    choice_label = st.selectbox("レースを選択", races_df["表示名"].tolist(), key="race_choice")
    chosen_race_id = races_df.loc[races_df["表示名"] == choice_label, "race_id"].values[0]

    # --- 2. 出走馬の持ちタイムを取得(スクレイピングはここだけ) ---------
    if st.button("このレースの出走馬の持ちタイムを取得", use_container_width=True, key="fetch"):
        fetch_compare_data(chosen_race_id)

    # --- 3. 取得済みデータがあれば、距離を切り替えて表示(再取得なし) ---
    if (
        st.session_state.compare_df is not None
        and st.session_state.compare_race_id == chosen_race_id
    ):
        compare_df = st.session_state.compare_df

        available_distances = sorted(compare_df["距離"].dropna().unique().tolist())
        distance_options = [AUTO_LABEL] + available_distances

        distance_choice = st.selectbox(
            "表示する距離 (m)", distance_options, index=0, key="dist_choice"
        )

        render_result_table(compare_df, distance_choice)
    elif st.session_state.compare_race_id is not None and st.session_state.compare_race_id != chosen_race_id:
        st.info("別のレースを選択しました。「出走馬の持ちタイムを取得」を押して取得してください。")
