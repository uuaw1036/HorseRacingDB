import time as _time

import pandas as pd
import streamlit as st

from scrape_netkeiba import (
    WAIT_SECONDS,
    get_horse_past_results,
    get_race_entries,
    prepare_for_compare,
)
from compare_times import best_time_per_horse, compare_by_distance

st.set_page_config(page_title="持ちタイム比較", page_icon="🏇", layout="centered")

st.title("🏇 持ちタイム比較ダッシュボード")
st.write(
    "netkeibaの**レースID**を入力するだけで、そのレースの出走馬全員の"
    "過去成績を自動取得し、持ちタイム（自己ベスト）を比較します。"
)

with st.expander("ℹ️ レースIDの調べ方"):
    st.write(
        "netkeibaでレースの出馬表ページを開き、URLの `race_id=` の"
        "後ろの数字をコピーしてください。\n\n"
        "例: `https://race.netkeiba.com/race/shutuba.html?race_id=202506050812`\n"
        "→ レースIDは `202506050812`"
    )

race_id = st.text_input("レースID", placeholder="例: 202506050812")

AUTO_LABEL = "自動（出走馬の過去実績すべてを距離ごとに表示）"
distance_options = [AUTO_LABEL, 1200, 1400, 1600, 1800, 2000, 2200, 2400, 2500, 3000, 3200]
distance_choice = st.selectbox("比較する距離 (m)", distance_options, index=0)

run = st.button("出走馬を取得して比較", use_container_width=True)

if run:
    rid = race_id.strip()
    if not rid:
        st.warning("レースIDを入力してください。")
    else:
        try:
            with st.spinner("出走馬一覧を取得中..."):
                entries = get_race_entries(rid)

            st.success(f"出走馬 {len(entries)}頭 を取得しました: {', '.join(entries['馬名'])}")

            # 1頭ずつ過去成績を取得しながら進捗を表示（スマホでも待ち時間が分かるように）
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
            else:
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
