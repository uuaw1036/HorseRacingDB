import time as _time
from datetime import date

import pandas as pd
import streamlit as st

from scrape_netkeiba import (
    WAIT_SECONDS,
    get_horse_past_results,
    get_race_entries,
    get_race_list,
    get_speed_index_by_umaban,
    parse_distance_column,
    parse_venue_name,
    prepare_for_compare,
)
from compare_times import best_time_per_horse, compare_by_distance, head_to_head_records, parse_time

st.set_page_config(page_title="持ちタイム比較", page_icon="🏇", layout="centered")

st.markdown("## 🏇 持ちタイム比較&対戦成績ダッシュボード")
st.write(
    "※30分間に5回以上の取得をする際はIPアドレスの変更を推奨します"
    "（Wi-Fiを変更・切断、VPN接続等）"
)

# 人気の色分け(セル背景色)
NINKI_COLORS = {
    1: "#FFF3B0",  # 黄色
    2: "#B3E5FC",  # 水色
    3: "#FFCDD2",  # 薄い赤
}

# 最終的な表示列(馬番〜着順、タイムが速い順に並ぶ)。horse_idは表示直前に落とす。
CENTRAL_DISPLAY_COLUMNS = [
    "馬番",
    "馬名",
    "人気",
    "馬場状態",
    "場",
    "タイム",
    "上がり3F",
    "通過",
    "馬体重",
    "斤量",
    "着順",
    "平均指数",
    "最高指数",
    "前走",
    "2走前",
    "3走前",
    "4走前",
    "5走前",
]

NAR_DISPLAY_COLUMNS = [
    "馬番",
    "馬名",
    "人気",
    "馬場状態",
    "場",
    "タイム",
    "上がり3F",
    "通過",
    "馬体重",
    "斤量",
    "着順",
]

SPEED_INDEX_VALUE_COLUMNS = ["平均指数", "最高指数", "前走", "2走前", "3走前", "4走前", "5走前"]

# --- セッション状態の初期化 -------------------------------------------
# race_list_df: 選択した開催日のレース候補一覧
# compare_df / compare_entries / compare_race_id: 一度取得した出走馬の
#   持ちタイムデータ。距離の切り替えはここから再計算するだけにして、
#   スクレイピングを何度も走らせないようにする。
# raw_df: 各出走馬の過去成績の生データ(persist_for_compare前)。
#   対戦成績(馬同士の勝敗)機能で使う。持ちタイム取得時に一度スクレイピング
#   したデータをそのまま再利用し、対戦成績のためだけの追加スクレイピングは
#   行わない。
# speed_index_df: jiro8サイトから取得した、出走馬ごとの過去5走分の
#   スピード指数(平均指数・最高指数)。持ちタイム表の一番右に表示する。
for key in [
    "race_list_df",
    "compare_df",
    "compare_entries",
    "compare_race_id",
    "raw_df",
    "speed_index_df",
]:
    if key not in st.session_state:
        st.session_state[key] = None


def fetch_compare_data(race_id: str, central: bool = True):
    """race_id の出走馬を取得し、持ちタイム比較用データを1回だけ取得してセッションに保存する。"""
    with st.spinner("出走馬一覧を取得中..."):
        entries = get_race_entries(race_id, central=central)

    st.success(f"出走馬 {len(entries)}頭 を取得しました。")

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

    # jiro8は中央競馬のレースしか扱っていないサイトのため、地方競馬のときは
    # そもそも取得を試みない(取得しても対応ページが無くエラーになるだけのため)。
    if central:
        with st.spinner("スピード指数(jiro8)を取得中..."):
            try:
                speed_index_df = get_speed_index_by_umaban(race_id)
            except Exception as e:
                st.warning(f"⚠️ スピード指数の取得に失敗しました: {e}")
                speed_index_df = pd.DataFrame(columns=["馬番"] + SPEED_INDEX_VALUE_COLUMNS)
    else:
        st.caption("※ スピード指数(jiro8)は中央競馬のみ対応のため、地方競馬では取得しません。")
        speed_index_df = pd.DataFrame(columns=["馬番"] + SPEED_INDEX_VALUE_COLUMNS)

    st.session_state.compare_df = compare_df
    st.session_state.compare_entries = entries
    st.session_state.compare_race_id = race_id
    st.session_state.raw_df = raw_df
    st.session_state.speed_index_df = speed_index_df


def _style_column(df: pd.DataFrame, column: str, color_map: dict):
    """指定した列だけをセル単位で色分けするStyler。

    pandas 2.1でStyler.applymap()は非推奨となり、後のバージョンでは
    Styler.map()に置き換わっている(環境によってはapplymapがもう存在しない)。
    両方のpandasバージョンで動くよう、mapがあればそちらを使う。
    """

    def _color(val):
        color = color_map.get(val)
        return f"background-color: {color}" if color else ""

    styler = df.style
    if hasattr(styler, "map"):
        return styler.map(_color, subset=[column])
    return styler.applymap(_color, subset=[column])


def style_by_ninki(df: pd.DataFrame):
    """人気列だけをセル単位で色分けするStyler。"""

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


def _rank_top3_colors(values: pd.Series, ascending: bool) -> pd.Series:
    """
    values を順位付けし、1〜3位に対応する背景色(人気と同じ配色)を、
    元の行位置(index)を保ったままSeriesとして返す(4位以下・NaNは空文字)。

    ascending=True: 値が小さいほど上位(タイム・上がり3Fなど)
    ascending=False: 値が大きいほど上位(スピード指数など)
    """
    numeric = pd.to_numeric(values, errors="coerce")
    ranks = numeric.rank(method="min", ascending=ascending)

    def _color(rank):
        if pd.isna(rank) or int(rank) not in NINKI_COLORS:
            return ""
        return f"background-color: {NINKI_COLORS[int(rank)]}"

    return ranks.apply(_color)


def speed_index_rank_colors(speed_df: pd.DataFrame):
    """
    speed_df (レース出走馬全体分の 馬番・平均指数・最高指数) を元に、
    レース全体で見た平均指数・最高指数の1〜3位を計算し、
    馬番 -> 背景色 の対応表 (dict) を返す ((平均指数用, 最高指数用) のタプル)。

    表(距離・馬場種別ごとの持ちタイムランキング)は、その距離・馬場種別を
    走ったことのある馬だけの部分集合になるため、表内だけで順位を計算すると
    同じ馬でも表によって色が変わってしまう。それを避けるため、必ず
    「このレースに出走する馬全体」を母集団にして順位を固定する。
    """
    empty = ({}, {})
    if speed_df is None or speed_df.empty:
        return empty

    def _rank_colors(values: pd.Series, umaban: pd.Series, ascending: bool) -> dict:
        numeric = pd.to_numeric(values, errors="coerce")
        ranks = numeric.rank(method="min", ascending=ascending)
        colors = {}
        for u, r in zip(umaban, ranks):
            if pd.isna(r) or pd.isna(u):
                continue
            rank_int = int(r)
            if rank_int in NINKI_COLORS:
                colors[int(u)] = f"background-color: {NINKI_COLORS[rank_int]}"
        return colors

    avg_colors = _rank_colors(speed_df["平均指数"], speed_df["馬番"], ascending=False)
    max_colors = _rank_colors(speed_df["最高指数"], speed_df["馬番"], ascending=False)
    return avg_colors, max_colors


def style_result_table(df: pd.DataFrame, speed_avg_colors: dict = None, speed_max_colors: dict = None):
    """
    持ちタイム比較表に色付けを行うStyler。
    - 人気: 1〜3位のセルに色付け(値そのものが順位)
    - タイム・上がり3F: 表内(=この距離・馬場種別を走ったことのある馬同士)で
      速い順に1〜3位のセルに色付け
      (タイムは表示用の文字列"1:33.4"のため、順位判定だけ秒数に変換する)
    - 平均指数・最高指数: 表内の順位ではなく、レースに出走する馬全体の中で
      高い順に1〜3位のセルに色付け(speed_index_rank_colors()で事前計算した
      馬番 -> 色の対応表をそのまま参照する。あわせて小数第2位までの表示に揃える)
    """
    speed_avg_colors = speed_avg_colors or {}
    speed_max_colors = speed_max_colors or {}

    styler = style_by_ninki(df)

    time_seconds = df["タイム"].apply(
        lambda t: parse_time(t) if pd.notna(t) and str(t).strip() else pd.NA
    )
    rank_targets = [
        ("タイム", time_seconds, True),
        ("上がり3F", df["上がり3F"], True),
    ]

    for column, values, ascending in rank_targets:
        if column not in df.columns:
            continue
        colors = _rank_top3_colors(values, ascending=ascending)

        def _apply(col: pd.Series, colors=colors):
            return [colors.get(idx, "") for idx in col.index]

        styler = styler.apply(_apply, subset=[column])

    def _apply_speed_colors(col: pd.Series, color_map: dict):
        return [
            color_map.get(int(u), "") if pd.notna(u) else ""
            for u in df["馬番"]
        ]

    if "平均指数" in df.columns:
        styler = styler.apply(
            lambda col: _apply_speed_colors(col, speed_avg_colors), subset=["平均指数"]
        )
    if "最高指数" in df.columns:
        styler = styler.apply(
            lambda col: _apply_speed_colors(col, speed_max_colors), subset=["最高指数"]
        )

    race_col_format = {col: "{:.1f}" for col in ["前走", "2走前", "3走前", "4走前", "5走前"] if col in df.columns}
    format_dict = {}
    if "平均指数" in df.columns:
        format_dict["平均指数"] = "{:.2f}"
    if "最高指数" in df.columns:
        format_dict["最高指数"] = "{:.2f}"
    format_dict.update(race_col_format)

    styler = styler.format(format_dict, na_rep="")

    return styler


def style_by_yuretsu(df: pd.DataFrame):
    """優劣列だけをセル単位で色分けするStyler。

    優(勝ち越し)は人気3位と同じ色、劣(負け越し)は人気2位と同じ色を使う。
    """
    yuretsu_colors = {
        "優": NINKI_COLORS[3],
        "劣": NINKI_COLORS[2],
    }
    return _style_column(df, "優劣", yuretsu_colors)


def build_table(
    compare_df: pd.DataFrame,
    entries: pd.DataFrame,
    distance: int,
    surface: str,
    venue: str = None,
    speed_df: pd.DataFrame = None,
    central: bool = True,
):
    """
    compare_df から指定の距離・馬場種別のランキングを作り、
    現在のレースの馬番(entriesの馬番)に差し替えて返す。

    venue を指定した場合は、さらに「場」列(過去成績を記録したレースの
    競馬場)がそのvenueと一致する行だけに絞り込む(=このレースと同じ
    競馬場での持ちタイムだけを見たい場合に使う)。

    speed_df を指定した場合は、jiro8サイトから取得した「馬番」ごとの
    平均指数・最高指数を持ちタイム表にマージする(このレースに対応する
    スピード指数が取得できていない場合はNoneのままでよく、その場合は
    平均指数・最高指数の列は空欄になる)。
    """
    result = compare_by_distance(compare_df, distance, surface)
    if result.empty:
        return result

    if venue:
        result = result[result["場"] == venue]
        if result.empty:
            return result
        # compare_by_distance()の時点でタイム昇順に並んでおり、
        # boolean条件での絞り込みでもその順序は保たれるため、
        # インデックスを振り直すだけでよい。
        result = result.reset_index(drop=True)

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

    display_cols = CENTRAL_DISPLAY_COLUMNS if central else NAR_DISPLAY_COLUMNS

    if central:
        if speed_df is not None and not speed_df.empty:
            speed_df = speed_df.copy()
            speed_df["馬番"] = pd.array(
                pd.to_numeric(speed_df["馬番"], errors="coerce"), dtype="Int64"
            )
            available_cols = ["馬番"] + [c for c in SPEED_INDEX_VALUE_COLUMNS if c in speed_df.columns]
            result = result.merge(speed_df[available_cols], on="馬番", how="left")
            for col in SPEED_INDEX_VALUE_COLUMNS:
                if col not in result.columns:
                    result[col] = pd.NA
        else:
            for col in SPEED_INDEX_VALUE_COLUMNS:
                result[col] = pd.NA
    else:
        for col in SPEED_INDEX_VALUE_COLUMNS:
            if col in result.columns:
                result = result.drop(columns=[col])

    result = result.fillna("")

    valid_cols = [c for c in display_cols if c in result.columns]
    return result[valid_cols]


def render_result_table(
    compare_df: pd.DataFrame,
    entries: pd.DataFrame,
    distance: int,
    surface: str,
    venue: str = None,
    speed_df: pd.DataFrame = None,
    central: bool = True,
):
    """セッションに保存済みのデータから、選択された距離・馬場種別のテーブルだけを表示する(再取得なし)。"""
    result = build_table(compare_df, entries, distance, surface, venue=venue, speed_df=speed_df, central=central)
    if result.empty:
        where = f"{venue}の{surface}{distance}m" if venue else f"{surface}{distance}m"
        st.info(f"出走馬の中に {where}を走った記録がある馬はいません。")
        return

    label = f"{venue}・{surface}{distance}m" if venue else f"{surface}{distance}m"
    st.success(f"分析完了（{label} 持ちタイムランキング）")
    
    speed_avg_colors, speed_max_colors = speed_index_rank_colors(speed_df) if central else ({}, {})
    st.dataframe(
        style_result_table(result, speed_avg_colors, speed_max_colors),
        use_container_width=True,
        hide_index=True,
    )


def render_head_to_head(raw_df: pd.DataFrame, entries: pd.DataFrame):
    """出走予定馬同士の対戦成績を表示する(持ちタイム取得時のデータを再利用。追加スクレイピングなし)。"""
    st.subheader("🥊 出走馬同士の対戦成績")

    entries_sorted = entries.sort_values("馬番", na_position="last")
    labels = [
        f"{row['馬番']}番 {row['馬名']}" if pd.notna(row["馬番"]) else row["馬名"]
        for _, row in entries_sorted.iterrows()
    ]
    horse_label_to_id = dict(zip(labels, entries_sorted["horse_id"]))
    selected_name = st.selectbox(
        "基準にする馬を選択", list(horse_label_to_id.keys()), key="h2h_horse"
    )
    target_id = horse_label_to_id[selected_name]

    summary_df, detail_df = head_to_head_records(raw_df, entries, target_id)

    if summary_df.empty:
        st.info(f"{selected_name} が他の出走予定馬と同じレースに出走した記録は見つかりませんでした。")
        return

    st.write(f"**{selected_name}** と過去に同じレースに出走したことのある出走予定馬との対戦成績:")
    st.dataframe(
        style_by_yuretsu(summary_df),
        use_container_width=True,
        hide_index=True,
        column_config={
            "馬番": st.column_config.NumberColumn("馬番", width="small"),
            "優劣": st.column_config.TextColumn("優劣", width="small"),
        },
    )

    with st.expander("対戦の詳細（レースごとの着順）を見る"):
        st.dataframe(
            detail_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "馬番": st.column_config.NumberColumn("馬番", width="small"),
            },
        )


# --- 1. 開催日からレースを選ぶ -----------------------------------------
racing_type = st.radio("競馬の種類", ["中央競馬", "地方競馬"], horizontal=True, key="racing_type")
is_central = racing_type == "中央競馬"

selected_date = st.date_input("開催日を選択", value=date.today())

if st.button("この日のレースを検索", use_container_width=True, key="search_races"):
    date_str = selected_date.strftime("%Y%m%d")
    try:
        with st.spinner("レース一覧を取得中..."):
            st.session_state.race_list_df = get_race_list(date_str, central=is_central)
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
    venues = list(dict.fromkeys(venues))

    if not venues:
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
        choice_label = st.selectbox(
            "レースを選択",
            venue_races["表示名"].tolist(),
            key=f"race_choice_{chosen_venue}",
        )
        venue_races = venue_races.loc[venue_races["表示名"] == choice_label]

    chosen_row = venue_races.iloc[0]
    chosen_race_id = chosen_row["race_id"]

    race_surface, race_distance = parse_distance_column(chosen_row.get("距離", ""))
    race_venue_clean = parse_venue_name(chosen_row.get("開催", "")) or None

    # --- 2. 出走馬の持ちタイムを取得(スクレイピングはここだけ) ---------
    if st.button("このレースの出走馬の情報を取得", use_container_width=True, key="fetch"):
        fetch_compare_data(chosen_race_id, central=is_central)

    # --- 3. 取得済みデータがあれば、距離・馬場種別を切り替えて表示(再取得なし) ---
    if st.session_state.compare_df is not None:
        compare_df = st.session_state.compare_df
        entries = st.session_state.compare_entries
        speed_df = st.session_state.speed_index_df

        if st.session_state.compare_race_id != chosen_race_id:
            st.caption(
                "※ 現在表示中のデータは以前取得したレースのものです。"
                "選択中のレースに更新するには「このレースの出走馬の情報を取得」を押してください。"
            )

        tab_time, tab_h2h = st.tabs(["🏇 持ちタイム", "🥊 対戦成績"])

        with tab_time:
            st.caption("※ 表右上にある目のアイコンから表示するカラムを変更できます")
            if is_central:
                st.caption("※ スピード指数は過去5走分のデータを使用しており、前日から取得できます")

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

                default_index = 0
                if race_surface and race_distance and (race_surface, race_distance) in available_pairs:
                    default_index = available_pairs.index((race_surface, race_distance))

                choice_label2 = st.selectbox(
                    "表示する距離 (m)", labels, index=default_index, key="dist_choice"
                )
                surface_choice, distance_choice = available_pairs[labels.index(choice_label2)]

                render_result_table(compare_df, entries, distance_choice, surface_choice, speed_df=speed_df, central=is_central)

            st.markdown("#### 📍 このレースの条件での持ちタイム")
            if race_surface and race_distance:
                render_result_table(
                    compare_df, entries, race_distance, race_surface, venue=race_venue_clean, speed_df=speed_df, central=is_central
                )
            else:
                st.info("このレース自体の馬場種別・距離が判別できませんでした。")

        with tab_h2h:
            render_head_to_head(st.session_state.raw_df, entries)
