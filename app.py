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

AUTO_LABEL = "自動（出走馬の過去実績すべてを距離ごとに表示）"
DISTANCE_OPTIONS = [AUTO_LABEL, 1200, 1400, 1600, 1800, 2000, 2200, 2400, 2500, 3000, 3200]

if "race_list_df" not in st.session_state:
    st.session_state.race_list_df = None


def run_comparison(race_id: str, distance_choice):
    """race_id を指定して出走馬を取得し、持ちタイムを比較・表示する。"""
    try:
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

        if distance_choice == AUTO_LABEL:
            result = best_time_per_horse(compare_df)
            result = result[["馬名", "距離", "馬場状態", "表示タイム"]].reset_index(drop=True)
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

    except Exception as e:
        st.error(f"エラーが発生しました: {e}")


tab1, tab2 = st.tabs(["📅 開催日から選ぶ", "🔢 レースIDを直接入力"])

# --- タブ1: 開催日を選んで、その日のレース一覧から選ぶ -----------------
with tab1:
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
    if races_df is not None and not races_df.empty:

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

        distance_choice_1 = st.selectbox(
            "比較する距離 (m)", DISTANCE_OPTIONS, index=0, key="dist1"
        )

        if st.button("このレースの持ちタイムを比較", use_container_width=True, key="compare1"):
            run_comparison(chosen_race_id, distance_choice_1)
    elif races_df is not None:
        st.info("この日のレースは見つかりませんでした。")

# --- タブ2: 従来通りレースIDを直接入力（フォールバック用） ------------
with tab2:
    st.write(
        "日付検索がうまくいかない場合は、こちらにレースIDを直接入力してください。"
    )
    with st.expander("ℹ️ レースIDの調べ方"):
        st.write(
            "netkeibaでレースの出馬表ページを開き、URLの `race_id=` の"
            "後ろの数字をコピーしてください。\n\n"
            "例: `https://race.netkeiba.com/race/shutuba.html?race_id=202506050812`\n"
            "→ レースIDは `202506050812`"
        )

    race_id_manual = st.text_input("レースID", placeholder="例: 202506050812", key="race_id_manual")
    distance_choice_2 = st.selectbox(
        "比較する距離 (m)", DISTANCE_OPTIONS, index=0, key="dist2"
    )

    if st.button("出走馬を取得して比較", use_container_width=True, key="compare2"):
        rid = race_id_manual.strip()
        if not rid:
            st.warning("レースIDを入力してください。")
        else:
            run_comparison(rid, distance_choice_2)
