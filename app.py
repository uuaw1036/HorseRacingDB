import streamlit as st
import pandas as pd
from scrape_netkeiba import get_multiple_horses, prepare_for_compare
from compare_times import compare_by_distance

# 画面のタイトル
st.title("🏇 持ちタイム比較ダッシュボード")

# ユーザーに馬のIDと距離を入力させるUI
st.write("出走馬のnetkeiba IDをカンマ区切りで入力してください。")
horse_ids_input = st.text_input("馬ID（例: ディープ, イクイノックス）", "2002100816, 2019105258")

# 距離を選択するプルダウン
distance = st.selectbox("比較する距離 (m)", [1200, 1400, 1600, 1800, 2000, 2200, 2400, 2500, 3000, 3200], index=4)

# ボタンが押されたときの処理
if st.button("データ取得＆比較"):
    # 入力された文字列をリストに変換
    horse_ids = [x.strip() for x in horse_ids_input.split(",")]
    
    # くるくる回るローディング表示
    with st.spinner('netkeibaから過去成績を取得中...'):
        try:
            # scrape_netkeiba.py の機能を使ってデータ取得
            raw_df = get_multiple_horses(horse_ids)
            
            if raw_df.empty:
                st.warning("データが取得できませんでした。")
            else:
                # 取得したデータを compare_times.py 用に整形
                compare_df = prepare_for_compare(raw_df)
                
                # 指定した距離で比較・ランキング作成
                result = compare_by_distance(compare_df, distance)
                
                if result.empty:
                    st.info(f"指定された馬の中に、{distance}mを走った記録はありません。")
                else:
                    st.success("分析完了！")
                    # スマホの画面幅に合わせて表を表示
                    st.dataframe(result, use_container_width=True)
                    
        except Exception as e:
            st.error(f"エラーが発生しました: {e}")
