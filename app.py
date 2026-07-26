import time as _time
from datetime import date

import pandas as pd
import streamlit as st

from scrape_netkeiba import (
    WAIT_SECONDS,
    get_horse_past_results,
    get_race_entries,
    get_race_list,
    parse_distance_column,
    prepare_for_compare,
)
from compare_times import best_time_per_horse, compare_by_distance, head_to_head_records

st.set_page_config(page_title="持ちタイム比較", page_icon="🏇", layout="centered")

st.title("🏇 持ちタイム比較ダッシュボード")
st.write(
    "これから行われるレースの出走馬について、過去の持ちタイム（自己ベスト）"
    "を自動取得して比較します。"
)

# 人気の色分け(セル背景色)
NINKI_COLORS = {
    1: "#FFF3B0",  # 黄色
    2: "#B3E5FC",  # 水色
    3: "#FFCDD2",  # 薄い赤
}

# 最終的な表示列(順位〜着順)。horse_idは表示直前に落とす。
DISPLAY_COLUMNS = [
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

# --- セッション状態の初期化 -------------------------------------------
# race_list_df: 選択した開催日のレース候補一覧
# compare_df / compare_entries / compare_race_id: 一度取得した出走馬の
#   持ちタイムデータ。距離の切り替えはここから再計算するだけにして、
#   スクレイピングを何度も走らせないようにする。
# raw_df: 各出走馬の過去成績の生データ(persist_for_compare前)。
#   対戦成績(馬同士の勝敗)機能で使う。持ちタイム取得時に一度スクレイピング
#   したデータをそのまま再利用し、対戦成績のためだけの追加スクレイピングは
#   行わない。
for key in ["race_list_df", "compare_df", "compare_entries", "compare_race_id", "raw_df"]:
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
    st.session_state.raw_df = raw_df


def style_by_ninki(df: pd.DataFrame):
    """人気列だけをセル単位で色分けするStyler。

    pandas 2.1でStyler.applymap()は非推奨となり、後のバージョンでは
    Styler.map()に置き換わっている(環境によってはapplymapがもう存在しない)。
    両方のpandasバージョンで動くよう、mapがあればそちらを使う。
    """

    def _color(val):
        try:
            v = int(val)
        except (TypeError, ValueError):
            return ""
        color = NINKI_COLORS.get(v)
        return f"background-color: {color}" if color else ""

    styler = df.style
    if hasattr(styler, "map"):
        return styler.map(_color, subset=["人気"])
    return styler.applymap(_color, subset=["人気"])


def build_table(compare_df: pd.DataFrame, entries: pd.DataFrame, distance: int, surface: str):
    """
    compare_df から指定の距離・馬場種別のランキングを作り、
    現在のレースの馬番(entriesの馬番)に差し替えて返す。
    """
    result = compare_by_distance(compare_df, distance, surface)
    if result.empty:
        return result

    # 過去成績時点の馬番・人気ではなく、今回のレースの馬番・人気に差し替える
    result = result.drop(columns=["馬番", "人気"]).merge(
        entries[["horse_id", "馬番", "人気"]], on="horse_id", how="left"
    )
    result = result.drop(columns=["horse_id"])

    # 人気はオッズ未確定の馬がいるとNaNが混ざりfloat化して"1.0"のような
    # 表示になってしまうため、null許容の整数型(Int64)にしておく。
    result["人気"] = pd.array(
        pd.to_numeric(result["人気"], errors="coerce"), dtype="Int64"
    )

    return result[DISPLAY_COLUMNS]


def render_result_table(compare_df: pd.DataFrame, entries: pd.DataFrame, distance: int, surface: str):
    """セッションに保存済みのデータから、選択された距離・馬場種別のテーブルだけを表示する(再取得なし)。"""
    result = build_table(compare_df, entries, distance, surface)
    if result.empty:
        st.info(f"出走馬の中に {surface}{distance}mを走った記録がある馬はいません。")
        return

    st.success(f"分析完了！（{surface}{distance}m 持ちタイムランキング）")
    st.dataframe(style_by_ninki(result), use_container_width=True, hide_index=True)


def render_head_to_head(raw_df: pd.DataFrame, entries: pd.DataFrame):
    """出走予定馬同士の対戦成績を表示する(持ちタイム取得時のデータを再利用。追加スクレイピングなし)。"""
    st.subheader("🥊 出走馬同士の対戦成績")

    horse_label_to_id = dict(zip(entries["馬名"], entries["horse_id"]))
    selected_name = st.selectbox(
        "基準にする馬を選択", list(horse_label_to_id.keys()), key="h2h_horse"
    )
    target_id = horse_label_to_id[selected_name]

    summary_df, detail_df = head_to_head_records(raw_df, entries, target_id)

    if summary_df.empty:
        st.info(f"{selected_name} が他の出走予定馬と同じレースに出走した記録は見つかりませんでした。")
        return

    st.write(f"**{selected_name}** と過去に同じレースに出走したことのある出走予定馬との対戦成績:")
    st.dataframe(summary_df, use_container_width=True, hide_index=True)

    with st.expander("対戦の詳細（レースごとの着順）を見る"):
        st.dataframe(detail_df, use_container_width=True, hide_index=True)


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

    def _race_label(row) -> str:
        parts = [
            p
            for p in [
                (row["R"] if row["R"] else ""),
                row["発走時刻"],
                row["レース名"],
                (f"({row['距離']})" if row.get("距離") else ""),
            ]
            if p
        ]
        return " / ".join(parts) if parts else row["race_id"]

    races_df = races_df.copy()

    # --- 1a. まず開催場所を選ぶ -----------------------------------------
    venues = [v for v in races_df["開催"].tolist() if v]
    # 順序を保ったまま重複を除く
    venues = list(dict.fromkeys(venues))

    if not venues:
        # 開催場所の情報が取れなかった場合は、従来通り全レースから直接選ぶ
        races_df["表示名"] = races_df.apply(
            lambda r: " / ".join(
                p
                for p in [r["発走時刻"], (r["R"] if r["R"] else ""), r["レース名"]]
                if p
            )
            or r["race_id"],
            axis=1,
        )
        choice_label = st.selectbox("レースを選択", races_df["表示名"].tolist(), key="race_choice")
        venue_races = races_df.loc[races_df["表示名"] == choice_label]
    else:
        chosen_venue = st.selectbox("開催場所を選択", venues, key="venue_choice")
        venue_races = races_df.loc[races_df["開催"] == chosen_venue].copy()
        venue_races["表示名"] = venue_races.apply(_race_label, axis=1)

        # --- 1b. 次にその開催場所の中からレース(R)を選ぶ -----------------
        # key に開催場所を含めることで、開催場所を切り替えたときに
        # 前の開催場所での選択が残ってエラーになるのを防ぐ。
        choice_label = st.selectbox(
            "レースを選択",
            venue_races["表示名"].tolist(),
            key=f"race_choice_{chosen_venue}",
        )
        venue_races = venue_races.loc[venue_races["表示名"] == choice_label]

    chosen_row = venue_races.iloc[0]
    chosen_race_id = chosen_row["race_id"]

    # レース一覧に載っている「芝1800m」のような表記から、このレース自体の
    # 馬場種別・距離を求めておく(取得後、デフォルトで表示する距離に使う)。
    race_surface, race_distance = parse_distance_column(chosen_row.get("距離", ""))

    # --- 2. 出走馬の持ちタイムを取得(スクレイピングはここだけ) ---------
    if st.button("このレースの出走馬の持ちタイムを取得", use_container_width=True, key="fetch"):
        fetch_compare_data(chosen_race_id)

    # --- 3. 取得済みデータがあれば、距離・馬場種別を切り替えて表示(再取得なし) ---
    # 別のレースを選んでも、再取得ボタンを押すまでは前回取得したデータを
    # そのまま表示し続ける(選択中のレースと取得済みデータのレースが
    # 異なる場合はその旨だけ注記する)。
    if st.session_state.compare_df is not None:
        compare_df = st.session_state.compare_df
        entries = st.session_state.compare_entries

        if st.session_state.compare_race_id != chosen_race_id:
            st.caption(
                "※ 現在表示中のデータは以前取得したレースのものです。"
                "選択中のレースに更新するには「出走馬の持ちタイムを取得」を押してください。"
            )

        pairs_df = (
            compare_df[["馬場種別", "距離"]]
            .dropna()
            .drop_duplicates()
            .sort_values(["馬場種別", "距離"])
        )
        available_pairs = list(pairs_df.itertuples(index=False, name=None))

        if not available_pairs:
            st.info("比較できる過去成績が見つかりませんでした。")
        else:
            labels = [f"{s}{d}m" for s, d in available_pairs]

            # デフォルトはこのレース自体の馬場種別・距離。データが無ければ先頭を使う。
            default_index = 0
            if race_surface and race_distance and (race_surface, race_distance) in available_pairs:
                default_index = available_pairs.index((race_surface, race_distance))

            choice_label2 = st.selectbox(
                "表示する距離 (m)", labels, index=default_index, key="dist_choice"
            )
            surface_choice, distance_choice = available_pairs[labels.index(choice_label2)]

            render_result_table(compare_df, entries, distance_choice, surface_choice)

        st.divider()
        render_head_to_head(st.session_state.raw_df, entries)
