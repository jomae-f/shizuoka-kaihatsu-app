import streamlit as st
import geopandas as gpd
import folium
from streamlit_folium import st_folium
from shapely.geometry import shape, Point
import pyproj
from functools import partial
from shapely.ops import transform
import os
import pandas as pd
import io  # 📄 PDFバイナリ制御用
from xhtml2pdf import pisa  # 📄 外部バイナリ不要の安全なPDF生成ライブラリ
import base64

# 1. 画面全体をワイドモードに設定
st.set_page_config(layout="wide")
st.title("静岡市 GIS開発要件判定システム")

# ----------------------------------------------------
# GISデータの読み込み（高速Feather版）
# ----------------------------------------------------
@st.cache_data
def load_spatial_files():
    try:
        gdf_shigaika = gpd.read_feather("data/shigaika.feather")
        gdf_chousei = gpd.read_feather("data/chousei.feather")
        gdf_tomoe = gpd.read_feather("data/tomoe.feather")
        
        town_path = "data/towns.feather"
        gdf_towns = gpd.read_feather(town_path) if os.path.exists(town_path) else None
        
        agri_path = "data/agri_shizuoka.feather"
        gdf_agri = gpd.read_feather(agri_path) if os.path.exists(agri_path) else None
        
        use_path = "data/use_districts.feather"
        gdf_use = gpd.read_feather(use_path) if os.path.exists(use_path) else None

        forest_path = "data/forest_shizuoka.feather"
        gdf_forest = gpd.read_feather(forest_path) if os.path.exists(forest_path) else None

        river_path = "data/river_shizuoka.feather"
        gdf_river = gpd.read_feather(river_path) if os.path.exists(river_path) else None

        flood_path = "data/flood_max_shizuoka.feather"
        if os.path.exists(flood_path):
            gdf_flood = gpd.read_feather(flood_path)
            gdf_flood['geometry'] = gdf_flood['geometry'].make_valid() if hasattr(gdf_flood, 'make_valid') else gdf_flood['geometry']
        else:
            gdf_flood = None

        dosha_path = "data/dosha_shizuoka.feather"
        if os.path.exists(dosha_path):
            gdf_dosha = gpd.read_feather(dosha_path)
            gdf_dosha['geometry'] = gdf_dosha['geometry'].make_valid() if hasattr(gdf_dosha, 'make_valid') else gdf_dosha['geometry']
        else:
            gdf_dosha = None

        # 🏺 埋蔵文化財データの読み込み
        cultural_path = "data/iseki_shizuoka.feather"
        gdf_cultural = gpd.read_feather(cultural_path) if os.path.exists(cultural_path) else None

        # 🚧 都市計画道路データの読み込み
        road_path = "data/plan-roads_shizuoka.feather"
        gdf_road = gpd.read_feather(road_path) if os.path.exists(road_path) else None
            
        return gdf_shigaika, gdf_chousei, gdf_tomoe, gdf_agri, gdf_towns, gdf_use, gdf_forest, gdf_river, gdf_flood, gdf_dosha, gdf_cultural, gdf_road
    except Exception as e:
        st.error(f"高速データの読み込み失敗: {e}")
        return None, None, None, None, None, None, None, None, None, None, None, None

@st.cache_data
def load_town_master():
    df = pd.read_csv("townname_shizuoka.csv", encoding="utf-8")
    def get_kana_group(kana):
        if not isinstance(kana, str) or len(kana) == 0: return "その他"
        first_char = kana[0]
        if first_char in "あいうえお": return "あ行"
        if first_char in "かきくけこがぎぐげご": return "か行"
        if first_char in "さしすせそざじずぜぞ": return "さ行"
        if first_char in "たちつてとだじづでどっ": return "た行"
        if first_char in "なにぬねの": return "な行"
        if first_char in "はひふへほばびぶべぼぱぴぷぺぽ": return "は行"
        if first_char in "まみむめも": return "ま行"
        if first_char in "やゆよゃゅょ": return "や行"
        if first_char in "らりるれろ": return "ら行"
        if first_char in "わをん": return "わ行"
        return "その他"
    df["50音分類"] = df["ふりがな"].apply(get_kana_group)
    return df

# 関数の戻り値を受け取る変数に gdf_cultural と gdf_road を追加
gdf_shigaika, gdf_chousei, gdf_tomoe, gdf_agri, gdf_towns, gdf_use, gdf_forest, gdf_river, gdf_flood, gdf_dosha, gdf_cultural, gdf_road = load_spatial_files()

def calculate_area_m2(geom):
    project = partial(pyproj.transform, pyproj.Proj(init='epsg:4326'), pyproj.Proj(init='epsg:6676'))
    return transform(project, geom).area

# ----------------------------------------------------
# 📄 xhtml2pdf：A4レイアウト
# ----------------------------------------------------
def generate_pdf(report_data):
    is_point_mode = report_data.get("geom_type") == "Point"
    area_text = f"{report_data['site_area']:,.1f} ㎡" if not is_point_mode else "― (地点指定のため面積なし)"
    
    if is_point_mode:
        toshi_status = "※敷地面積が1,000㎡（調整区域は500㎡）以上となる場合は開発許可申請の手続きが必要です。"
        agri_status = "⚠️ 近傍に農地あり (要窓口確認)" if report_data["agri_near"] else "✅ 周辺に対象区域なし"
        forest_status = "⚠️ 近傍に対象森林あり (要窓口確認)" if report_data["forest_near"] else "✅ 周辺に対象区域なし"
        # 🚧 都市計画道路（点指定）
        road_status = "⚠️ 近傍に計画道路あり (要窓口確認)" if report_data.get("road_near") else "✅ 周辺に対象区域なし"
        # 🏺 文化財保護法（点指定）
        cultural_status = "⚠️ 埋蔵文化財包蔵地の近傍 (要事前協議)" if report_data.get("cultural_near") else "✅ 周辺に対象区域なし"
    else:
        toshi_status = "⚠️ 手続き必要" if report_data["is_dev_required"] else "✅ 対象外"
        agri_status = f"⚠️ 手続き必要 (重複: {(report_data['agri_area']/report_data['site_area'])*100.0:.1f}%)" if report_data["agri_area"] > 1.0 else "✅ 対象外"
        forest_status = f"⚠️ 要確認 (重複: {(report_data['forest_area']/report_data['site_area'])*100.0:.1f}%)" if report_data["forest_area"] > 1.0 else "✅ 対象外"
        # 🚧 都市計画道路（区域描画）
        road_status = f"⚠️ 計画道路内 (重複: {(report_data.get('road_area', 0)/report_data['site_area'])*100.0:.1f}%)" if report_data.get('road_area', 0) > 1.0 else "✅ 対象外"
        # 🏺 文化財保護法（区域描画）
        cultural_status = f"⚠️ 包蔵地内 (重複: {(report_data.get('cultural_area', 0)/report_data['site_area'])*100.0:.1f}%)" if report_data.get('cultural_area', 0) > 1.0 else "✅ 対象外"
    
    if report_data["check_morido"]:
        morido_status = "⚠️ 規制対象規模" if report_data["morido_required"] else "✅ 対象外"
    else:
        morido_status = "― (計画なし)"
        
    flood_status = f"⚠️ 該当 ({report_data['flood_river_name']})" if report_data.get("flood_hit") else "✅ 対象外"
    
    # 1km以上の場合は「1km以内に主要河川なし」に固定
    river_status = f"{report_data['nearest_river_name']}まで約{report_data['nearest_river_dist']}m" if report_data.get("has_river_dist") else "1km以内に主要河川なし"

    if is_point_mode:
        dosha_status = "⚠️ 近傍50m以内に土砂災害警戒区域が存在します (要確認)" if report_data["dosha_near"] else "✅ 周辺に対象区域なし"
    else:
        if report_data.get("dosha_hit"):
            labels = []
            if report_data["dosha_red_area"] > 1.0:
                labels.append(f"🔴 特別警戒(レッド): {(report_data['dosha_red_area']/report_data['site_area'])*100.0:.1f}%")
            if report_data["dosha_yellow_area"] > 1.0:
                labels.append(f"🟡 警戒(イエロー): {(report_data['dosha_yellow_area']/report_data['site_area'])*100.0:.1f}%")
            dosha_status = "⚠️ " + " / ".join(labels)
        else:
            dosha_status = "✅ 対象外"

    tech_section = ""
    if not is_point_mode:
        basis_text = "巴川流域整備計画基準" if report_data.get("is_tomoe") else "静岡市指導要綱基準（目安）"
        tech_section = f"""
        <h3>2. 技術基準・附帯施設要件</h3>
        <table class="main-table">
            <tr>
                <th>雨水調整池</th>
                <td>
                    {"検討必要" if report_data['site_area'] >= 1000 else "免除"}<br>
                    <span style="font-size: 8.5pt; color:#555555;">目安: {report_data['vol_min']:,.0f}～{report_data['vol_max']:,.0f} ㎥ ({basis_text})</span>
                </td>
                <th>緑地確保</th>
                <td>
                    {"必要" if ("静岡市" in report_data['loc_label'] and report_data['site_area'] >= 1000) else "免除"}<br>
                    <span style="font-size: 8.5pt; color:#666666;">目安: {report_data['max_green']:,.1f} ㎡ 以上 ({report_data['max_basis']})</span>
                </td>
            </tr>
        </table>
        """

    html_content = f"""
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            @page {{ size: a4; margin: 1.5cm; }}
            body {{ font-family: "HeiseiMin-W3", serif; color: #333333; font-size: 10pt; line-height: 1.6; }}
            .header {{ border-bottom: 2px solid #003366; padding-bottom: 8px; margin-bottom: 20px; }}
            .title {{ font-size: 18pt; font-weight: bold; color: #003366; }}
            table.meta-table {{ width: 100%; border-collapse: collapse; margin-bottom: 20px; }}
            table.meta-table td {{ padding: 8px 12px; border: 1px solid #cccccc; vertical-align: middle; }}
            .meta-label {{ color: #555555; font-weight: bold; font-size: 9pt; width: 25%; background-color: #f8f9fa; }}
            h3 {{ font-size: 12pt; font-weight: bold; border-left: 5px solid #003366; padding-left: 10px; margin-top: 20px; margin-bottom: 10px; color: #003366; }}
            table.main-table {{ width: 100%; border-collapse: collapse; margin-bottom: 15px; }}
            table.main-table th, table.main-table td {{ border: 1px solid #cccccc; padding: 8px 10px; font-size: 10pt; text-align: left; }}
            table.main-table th {{ background-color: #f2f2f2; width: 25%; font-weight: bold; }}
            .footer {{ text-align: center; font-size: 8pt; color: #888888; margin-top: 30px; border-top: 1px dashed #cccccc; padding-top: 10px; }}
        </style>
    </head>
    <body>
        <div class="header">
            <div class="title">土地開発要件 判定結果レポート ({"地点指定モード" if is_point_mode else "区域描画モード"})</div>
        </div>

        <table class="meta-table">
            <tr><td class="meta-label">📍 敷地所在</td><td><strong>{report_data['loc_label']}</strong></td></tr>
            <tr><td class="meta-label">📐 敷地面積</td><td><strong>{area_text}</strong></td></tr>
            <tr><td class="meta-label">🌐 区域区分</td><td><strong>{report_data['current_zone']}</strong></td></tr>
            <tr><td class="meta-label">🏢 用途地域</td><td><strong>{report_data['target_use_name']}</strong></td></tr>
            <tr><td class="meta-label">📐 建蔽率 / 容積率</td><td><strong>{report_data.get('kinpei_str', '―')} / {report_data.get('youseki_str', '―')}</strong></td></tr>
        </table>

        <h3>1. 主要法令に基づく手続要件</h3>
        <table class="main-table">
            <tr>
                <th>都市計画法</th><td>{toshi_status}</td>
                <th>農地法</th><td>{agri_status}</td>
            </tr>
            <tr>
                <th>盛土規制法</th><td>{morido_status}</td>
                <th>森林法</th><td>{forest_status}</td>
            </tr>
            <tr>
                <th>都市計画道路</th><td>{road_status}</td>
                <th>文化財保護法</th><td>{cultural_status}</td>
            </tr>
        </table>

        {tech_section}

        <h3>{"2" if is_point_mode else "3"}. 水害・土砂・河川リスク</h3>
        <table class="main-table">
            <tr>
                <th>洪水浸水想定</th><td>{flood_status}</td>
                <th>最寄り主要河川</th><td>{river_status}</td>
            </tr>
            <tr>
                <th>土砂災害警戒区域</th><td colspan="3">{dosha_status}</td>
            </tr>
        </table>

        <div class="footer">
            静岡市 GIS開発要件判定システム - 暫定自動演算結果（実務時は各窓口へ要相談）
        </div>
    </body>
    </html>
    """
    pdf_buffer = io.BytesIO()
    pisa_status = pisa.CreatePDF(io.BytesIO(html_content.encode("utf-8")), dest=pdf_buffer, encoding='utf-8')
    if pisa_status.err: raise Exception("HTMLからPDFへの変換処理でエラーが発生しました。")
    return pdf_buffer.getvalue()

# ----------------------------------------------------
# 💡 ポップアップ（ダイアログ）の定義
# ----------------------------------------------------
@st.dialog("📊 開発要件 判定結果レポート", width="large")
def show_result_dialog(report_data):
    loc_label = report_data["loc_label"]
    is_point_mode = report_data.get("geom_type") == "Point"
    area_display = f"{report_data['site_area']:,.1f} ㎡" if not is_point_mode else "―"
    
    box_html = (
        f'<div style="background-color: #f0f2f6; padding: 14px; border-radius: 8px; margin-bottom: 20px; display: flex; justify-content: space-between; gap: 10px;">'
        f'  <div style="flex: 1;">'
        f'    <div style="font-size: 0.8rem; color: #555;">📍 敷地所在</div>'
        f'    <div style="font-size: 1.0rem; font-weight: bold; color: #111;">{loc_label}</div>'
        f'  </div>'
        f'  <div style="flex: 1; border-left: 1px solid #ccc; padding-left: 15px;">'
        f'    <div style="font-size: 0.8rem; color: #555;">📐 敷地面積</div>'
        f'    <div style="font-size: 1.2rem; font-weight: bold; color: #111;">{area_display}</div>'
        f'  </div>'
        f'  <div style="flex: 1; border-left: 1px solid #ccc; padding-left: 15px;">'
        f'    <div style="font-size: 0.8rem; color: #555;">🌐 区域区分</div>'
        f'    <div style="font-size: 1.0rem; font-weight: bold; color: #111;">{report_data["current_zone"]}</div>'
        f'  </div>'
        f'  <div style="flex: 1; border-left: 1px solid #ccc; padding-left: 15px;">'
        f'    <div style="font-size: 0.8rem; color: #555;">🏢 用途地域</div>'
        f'    <div style="font-size: 1.0rem; font-weight: bold; color: #111;">{report_data["target_use_name"]}</div>'
        f'  </div>'
        f'  <div style="flex: 1; border-left: 1px solid #ccc; padding-left: 15px;">'
        f'    <div style="font-size: 0.8rem; color: #555;">📐 建蔽率 / 容積率</div>'
        f'    <div style="font-size: 1.0rem; font-weight: bold; color: #111;">{report_data.get("kinpei_str", "―")} / {report_data.get("youseki_str", "―")}</div>'
        f'  </div>'
        f'</div>'
    )
    st.markdown(box_html, unsafe_allow_html=True)
    
    try:
        pdf_data = generate_pdf(report_data)
        st.download_button(
            label="📄 判定結果レポートをPDFでダウンロード (A4印刷向き)",
            data=pdf_data,
            file_name=f"開発要件判定レポート_{loc_label.replace(', ', '_')}.pdf",
            mime="application/pdf",
            use_container_width=True
        )
    except Exception as e:
        st.error(f"PDF生成コンポーネントの準備に失敗しました: {e}")
        
    st.markdown("---")
    diag_col1, diag_col2 = st.columns(2)
    
    with diag_col1:
        if is_point_mode:
            st.info("ℹ️ **【都市計画法】**\n\n地点指定（ピンポイント）モードです。開発行為を伴う敷地全体の面積が**1,000㎡以上（市街化調整区域の場合は500㎡以上）**となる場合は、開発許可申請の手続きが必要です。")
        else:
            if report_data["is_dev_required"]:
                st.error("🚨 **【都市計画法】**\n\n開発許可申請の手続きが必要です。")
            else:
                st.success("✅ **【都市計画法】**\n\n開発許可の申請は不要です。")
            
        if is_point_mode:
            if report_data["agri_near"]:
                st.warning("🚜 **【農地法（近傍判定）】**\n\n指定地点の**50m以内**に農地区域が存在します。敷地境界が重なる、または該当する可能性があるため、窓口で詳細な境界を確認してください。")
            else:
                st.success("✅ **【農地法】**\n\n周辺50m以内に対象の農地区域はありません。")
        else:
            if report_data["input_mode"] == "🗺️ 地図に描画" and report_data["agri_area"] > 1.0:
                agri_p = (report_data["agri_area"] / report_data["site_area"]) * 100.0
                st.error(f"🚜 **【農地法】**\n\n敷地内に農地区域が重複しています（重複率: {agri_p:.1f}%）。手続きが必要です。")
            else:
                st.success("✅ **【農地法】**\n\n農地区域外です。")
            
        if report_data["check_morido"]:
            if report_data["morido_required"]:
                st.warning("🚧 **【盛土規制法】**\n\n規制対象規模です。許可手続き必要。")
            else:
                st.success("✅ **【盛土規制法】**\n\n対象外、または届出・許可を要しない規模です。")
            
        if is_point_mode:
            if report_data["forest_near"]:
                st.warning("🌲 **【森林法（近傍判定）】**\n\n指定地点の**50m以内**に地域森林計画の対象森林が存在します。開発許可や事前届出等の手続き必要となる可能性があるため、窓口での確認を推奨します。")
            else:
                st.success("✅ **【森林法】**\n\n周辺50m以内に対象森林はありません。")
        else:
            if report_data["input_mode"] == "🗺️ 地図に描画" and report_data["forest_area"] > 1.0:
                forest_p = (report_data["forest_area"] / report_data["site_area"]) * 100.0
                st.error(f"🌲 **【森林法】**\n\n敷地内に地域森林計画の対象森林が重複しています（重複率: {forest_p:.1f}%）。\n\n開発許可や届出等の手続き必要となる可能性があります。")
            else:
                st.success("✅ **【森林法】**\n\n対象の森林区域外です。")

        # 🚧 都市計画道路の表示（新規追加）
        if is_point_mode:
            if report_data.get("road_near"):
                st.warning("🚧 **【都市計画道路（近傍判定）**\n\n指定地点の**50m以内**に都市計画道路の決定区域があります。計画線に抵触するかどうか、窓口での詳細な確認が必要です。")
            else:
                st.success("✅ **【都市計画道路】**\n\n周辺50m以内に都市計画道路の決定区域はありません。")
        else:
            if report_data["input_mode"] == "🗺️ 地図に描画" and report_data.get("road_area", 0) > 1.0:
                road_p = (report_data["road_area"] / report_data["site_area"]) * 100.0
                st.error(f"🚧 **【都市計画道路】**\n\n開発区域内に都市計画道路の計画区域が重複しています（重複率: {road_p:.1f}%）。\n\n⚠️ 都市計画法第53条に基づく建築許可申請などの手続きが必要となる可能性が高いです。")
            else:
                st.success("✅ **【都市計画道路】**\n\n都市計画道路の区域外です。")

        # 🏺 埋蔵文化財包蔵地の表示（新規追加）
        if is_point_mode:
            if report_data.get("cultural_near"):
                st.warning("🏺 **【文化財保護法（近傍判定）】**\n\n指定地点の**50m以内**に周知の埋蔵文化財包蔵地が存在します。掘削を伴う工事を行う場合、事前協議や届出が必要になる可能性があるため要確認です。")
            else:
                st.success("✅ **【埋蔵文化財包蔵地】**\n\n周辺50m以内に周知の埋蔵文化財包蔵地はありません。")
        else:
            if report_data["input_mode"] == "🗺️ 地図に描画" and report_data.get("cultural_area", 0) > 1.0:
                cultural_p = (report_data["cultural_area"] / report_data["site_area"]) * 100.0
                st.error(f"🏺 **【埋蔵文化財包蔵地】**\n\n開発区域の一部または全部が**周知の埋蔵文化財包蔵地に含まれています**（重複率: {cultural_p:.1f}%）。\n\n⚠️ 土木工事等の着手日の60日前までに、文化財保護法第93条に基づく事前の届出（都道府県・政令市宛）が必要です。")
            else:
                st.success("✅ **【埋蔵文化財包蔵地】**\n\n周知の埋蔵文化財包蔵地の対象外です。")

    with diag_col2:
        if not is_point_mode:
            if report_data["site_area"] >= 1000:
                basis_text = "巴川流域整備計画基準" if report_data["is_tomoe"] else "静岡市開発指導要綱基準（目安）"
                st.info(f"💧 **【調整池】**\n\n設置の検討が必要です。\n\n必要容量の目安:\n**{report_data['vol_min']:,.0f} ～ {report_data['vol_max']:,.0f} ㎥**\n\n（適用: {basis_text}）")
            else:
                st.success("✅ **【調整池】**\n\n指導基準に基づく設置義務はありません。")
                
            if "静岡市" in loc_label and report_data["site_area"] >= 1000:
                notice_text = "\n\n⚠️ 事業目的が未選択のため、一般基準（みどり条例）で暫定算出しています。" if report_data["purpose_none"] else ""
                st.warning(
                    f"🌲 **【緑地】**\n\n"
                    f"確保が必要な緑地面積の目安:\n**{report_data['max_green']:,.1f} ㎡ 以上**\n\n"
                    f"📝 算出根拠: {report_data['max_basis']}{notice_text}"
                )
            else:
                st.success("✅ **【緑地】**\n\n静岡市外、または敷地1,000㎡未満のため緑地の確保義務はありません。")

        if report_data["input_mode"] == "🗺️ 地図に描画":
            if report_data["gdf_flood_none"]:
                st.caption("ℹ️ 洪水浸水想定区域データが見つかりません。")
            elif report_data["flood_hit"]:
                st.error(
                    f"🌊 **【洪水浸水想定区域】**\n\n"
                    f"想定最大規模の想定浸水域に**該当しています**。\n\n"
                    f"* **対象河川:** {report_data['flood_river_name']}\n"
                    f"* **浸水深ランク:** コード {report_data['flood_rank_code']} : {report_data['flood_desc']}\n\n"
                    f"⚠️ 重大事項説明の対象エリアです。建築計画時の床高確認や避難確保計画等について窓口へ相談をお勧めします。"
                )
            else:
                st.success("✅ **【洪水浸水想定区域】**\n\n想定最大規模の洪水浸水想定区域外です。")

        if is_point_mode:
            if report_data["dosha_near"]:
                st.error("🚨 **【土砂災害警戒区域（近傍判定）】**\n\n指定地点の**50m以内**に土砂災害警戒区域（イエロー・レッド）が存在します。敷地近傍のため、ハザードマップ等で詳細な位置関係を必ずご確認ください。")
            else:
                st.success("✅ **【土砂災害警戒区域】**\n\n周辺50m以内に土砂災害警戒区域はありません。")
        else:
            if report_data.get("gdf_dosha_none"):
                st.caption("ℹ️ 土砂災害警戒区域データが見つかりません。")
            elif report_data.get("dosha_hit"):
                warning_msg = "🚨 **【土砂災害警戒区域】**\n\n開発区域の一部または全部が**土砂災害警戒区域に含まれています**。\n\n" if report_data["dosha_red_area"] > 1.0 else "⚠️ **【土砂災害警戒区域】**\n\n開発区域の一部または全部が**土砂災害警戒区域に含まれています**。\n\n"
                if report_data["dosha_red_area"] > 1.0:
                    red_p = (report_data["dosha_red_area"] / report_data["site_area"]) * 100.0
                    warning_msg += f"* **特別警戒区域 (レッドゾーン):** 重複 {report_data['dosha_red_area']:,.1f} ㎡ ({red_p:.1f}%)\n"
                if report_data["dosha_yellow_area"] > 1.0:
                    yellow_p = (report_data["dosha_yellow_area"] / report_data["site_area"]) * 100.0
                    warning_msg += f"* **警戒区域 (イエローゾーン):** 重複 {report_data['dosha_yellow_area']:,.1f} ㎡ ({yellow_p:.1f}%)\n"
                if report_data["dosha_red_area"] > 1.0:
                    warning_msg += "\n⚠️ レッドゾーン内での特定の開発行為には制限、構造規制が課されます。建築・開発前に必ず担当窓口へご確認ください。"
                    st.error(warning_msg)
                else:
                    warning_msg += "\n⚠️ イエローゾーン内での開発・建築時は、ハザードマップ等による避難体制の確認および重大事項説明への記載が必要です。窓口へご確認ください。"
                    st.warning(warning_msg)
            else:
                st.success("✅ **【土砂災害警戒区域】**\n\n土砂災害警戒区域（イエロー・レッド）の対象外です。")

        # 🛠️ 河川情報の表示処理（インデント外に移譲：ピン指定モードとポリゴンモード共通化）
        if report_data["input_mode"] == "🗺️ 地図に描画":
            if report_data["has_river_dist"]:
                st.info(
                    f"ℹ️ **【周辺の河川情報】**\n\n"
                    f"もっとも近い主要河川は **{report_data['nearest_river_name']}（{report_data['nearest_river_class']}）** です。\n\n"
                    f"* **直線距離:** 約 {report_data['nearest_river_dist']} メートル\n\n"
                    f"※河川境界からの距離によっては河川法に基づく許可申請が必要な場合があります。"
                )
            else:
                st.success("✅ **【周辺の河川情報】**\n\n1km以内に該当する主要河川はありません。")

# ----------------------------------------------------
# 📐 画面レイアウトの2分割（2:8 比率）
# ----------------------------------------------------
col_left, col_center = st.columns([2, 8])

site_area = 0.0
city_name = "静岡市"
detailed_location = "未選択"
has_data = False
use_choice = "未確定" 
current_zone = "未確定"
geom_type = "Polygon"
kinpei_str = "未確定"
youseki_str = "未確定"

# 🚧 都市計画道路、🏺 埋蔵文化財の面積変数を追加
shigaika_p, chousei_p, tomoe_area, agri_area, forest_area, road_area, cultural_area = 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
# 🚧 都市計画道路、🏺 埋蔵文化財の近傍フラグ変数を追加
agri_near, forest_near, dosha_near, road_near, cultural_near = False, False, False, False, False

min_distance_m = float('inf')
nearest_river_name = "名称不明の河川"
nearest_river_class = ""
has_river_dist = False
nearest_river_dist = None

flood_hit = False
flood_river_name = ""
flood_rank_code = ""
flood_desc = ""

dosha_hit = False
dosha_red_area = 0.0
dosha_yellow_area = 0.0

# 空の辞書として初期化した後、空間判定ロジックを通過したデータがここに格納される想定です
report_data = {}

# ====================================================
# 🎛️ 画面左側（比率2）：条件設定
# ====================================================
with col_left:
    st.subheader("⚙️ 条件設定")
    
    st.markdown("**【敷地情報の入力方法】**")
    input_mode = st.radio("敷地情報の入力方法", ["🗺️ 地図に描画", "✍️ 手入力"], label_visibility="collapsed")
    st.markdown("---")
    
    if input_mode == "✍️ 手入力":
        city_name = st.selectbox("所在", ["静岡市"])
        try:
            df_town = load_town_master()
            col_ward, col_kana = st.columns(2)
            with col_ward:
                desired_ward_order = ["葵区", "駿河区", "清水区"]
                actual_wards = df_town["区名"].unique()
                ward_list = [w for w in desired_ward_order if w in actual_wards] + [w for w in actual_wards if w not in desired_ward_order]
                selected_ward = st.selectbox("区", ward_list, index=0)
            with col_kana:
                df_ward_filtered = df_town[df_town["区名"] == selected_ward]
                kana_list = list(df_ward_filtered["50音分類"].unique())
                kana_order = {"あ行":1, "か行":2, "さ行":3, "た行":4, "な行":5, "は行":6, "ま行":7, "や行":8, "ら行":9, "わ行":10, "その他":11}
                kana_list = sorted([k for k in kana_list if k in kana_order], key=lambda x: kana_order[x])
                selected_kana = st.selectbox("50音", kana_list)
                
            df_town_filtered = df_ward_filtered[df_ward_filtered["50音分類"] == selected_kana]
            df_town_sorted = df_town_filtered[["町名", "ふりがな"]].drop_duplicates().sort_values("ふりがな")
            town_list = df_town_sorted["町名"].tolist()
            selected_town = st.selectbox("町名", town_list)
            detailed_location = f"静岡市{selected_ward}{selected_town}"
        except Exception as e:
            st.error(f"町名CSVの読み込みエラー: {e}")
            detailed_location = "静岡市"
            
        site_area = st.number_input("敷地面積 (㎡)", min_value=0.0, value=0.0, step=100.0)
        has_data = True if site_area > 0 else False
        current_zone = st.selectbox("区域区分", ["市街化区域", "市街化調整区域", "都市計画区域外"])
        use_choice = st.selectbox("用途地域", ["準工業・工業・工専以外", "準工業地域", "工業地域・工業専用地域"])

    st.markdown("**【事業目的の選択】**")
    
    poses = {
        "1":  {"label": "工場（製造業）", "cat": "building", "is_factory_law": True},
        "2":  {"label": "工場（非製造業）", "cat": "building", "is_factory_law": False},
        "3":  {"label": "自家用倉庫", "cat": "building", "is_factory_law": False},
        "4":  {"label": "営業用倉庫", "cat": "building", "is_factory_law": False},
        "5":  {"label": "事務所・オフィス", "cat": "building", "is_factory_law": False},
        "6":  {"label": "店舗・飲食店", "cat": "building", "is_factory_law": False},
        "7":  {"label": "住宅（個人住宅・共同住宅）", "cat": "building", "is_factory_law": False},
        "8":  {"label": "社会福祉施設・学校・病院", "cat": "building", "is_factory_law": False},
        "10": {"label": "コンクリート・アスファルトプラント", "cat": "spec_1", "is_factory_law": False},
        "11": {"label": "クラッシャープラント", "cat": "spec_1", "is_factory_law": False},
        "12": {"label": "危険物貯蔵所", "cat": "spec_1", "is_factory_law": False},
        "20": {"label": "ゴルフ場", "cat": "spec_2_always", "is_factory_law": False},
        "21": {"label": "運動施設", "cat": "spec_2_check", "is_factory_law": False},
        "22": {"label": "レジャー施設", "cat": "spec_2_check", "is_factory_law": False},
        "23": {"label": "墓地", "cat": "spec_2_check", "is_factory_law": False},
        "30": {"label": "太陽光発電施設（野立て）", "cat": "other_shokei", "is_factory_law": False},
        "31": {"label": "資材置場・駐車場", "cat": "other_shokei", "is_factory_law": False},
        "32": {"label": "資材・スクラップの集積場", "cat": "other_shokei", "is_factory_law": False},
    }
    
    purpose_options = ["―"] + [v["label"] for v in poses.values()]
    selected_label = st.selectbox("事業目的", purpose_options, index=0)
    
    is_factory_law = False  
    selected_purpose = None
    building_area = 0.0
    
    if selected_label != "―":
        p_options = {v["label"]: k for k, v in poses.items()}
        selected_purpose = poses[p_options[selected_label]]
        is_factory_law = selected_purpose["is_factory_law"]
        if selected_purpose["cat"] == "building":
            building_area = st.number_input("建築面積 (㎡)", min_value=0.0, value=0.0, step=10.0)

    st.markdown("---")
    
    check_morido = st.checkbox("**📐 切土・盛土計画あり**", value=False)
    morido_area, kiri_height, mori_height = 0.0, 0.0, 0.0
    if check_morido:
        st.caption("以下に計画規模を入力してください")
        morido_area = st.number_input("切土・盛土を行う面積 (㎡)", min_value=0.0, value=0.0, key="morido_area_input", step=10.0)
        kiri_height = st.number_input("切土の最大高さ (m)", min_value=0.0, value=0.0, key="kiri_height_input", step=0.1)
        mori_height = st.number_input("盛土の最大高さ (m)", min_value=0.0, value=0.0, step=0.1)


# ====================================================
# 🗺️ 画面中央・右側（比率8）：地図の処理＆空間判定
# ====================================================
with col_center:
    col_title, col_btn = st.columns([80, 20])
    with col_title:
        st.subheader("🗺️ 開発区域の指定")
        
    m = folium.Map(location=[34.9755, 138.3826], zoom_start=14, max_zoom=20, control_scale=True)
    folium.TileLayer(tiles='https://mt1.google.com/vt/lyrs=m&x={x}&y={y}&z={z}', attr='Google', name='Google マップ (標準)', max_zoom=20).add_to(m)
    folium.TileLayer(tiles='https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}', attr='Google', name='Google 航空写真', max_zoom=20).add_to(m)
    folium.TileLayer(tiles='https://cyberjapandata.gsi.go.jp/xyz/seamlessphoto/{z}/{x}/{y}.jpg', attr='国土地理院', name='国土地理院 航空写真', max_zoom=18).add_to(m)

    from folium.plugins import Draw
    Draw(
        export=False, 
        position='topleft', 
        draw_options={
            'polyline': False, 'circle': False, 'rectangle': False, 
            'marker': True, 'circlemarker': False, 'polygon': True
        }
    ).add_to(m)
    
    # 🛠️ 削除ボタンをクリックしただけで即時一括クリアするJavaScriptコード
    clear_script = """
    <script>
    document.addEventListener("DOMContentLoaded", function() {
        var checkExist = setInterval(function() {
            var deleteBtn = document.querySelector('.leaflet-draw-edit-remove');
            if (deleteBtn) {
                clearInterval(checkExist);
                // 既存のクリックイベントを上書き/フックして即時削除を走らせる
                deleteBtn.addEventListener('click', function(e) {
                    e.preventDefault();
                    e.stopPropagation();
                    // Foliumが作成した全世界のLeafletマップオブジェクトを探す
                    var maps = Object.values(window).filter(v => v instanceof L.Map);
                    maps.forEach(function(map) {
                        map.eachLayer(function(layer) {
                            // 描画されたPolygonやMarkerのレイヤーグループ（drawnItemsなど）を検知してクリア
                            if (layer instanceof L.FeatureGroup && typeof layer.clearLayers === 'function') {
                                layer.clearLayers();
                            }
                        });
                    });
                    // Streamlitにクリアされた描画情報を即座に反映させるシグナル
                    var clearAllBtn = document.querySelector('.leaflet-draw-actions a[title="Cancel drawing"]');
                    if(clearAllBtn) clearAllBtn.click();
                });
            }
        }, 100);
    });
    </script>
    """
    m.get_root().html.add_child(folium.Element(clear_script))
    folium.LayerControl(position='topright').add_to(m)

    map_data = st_folium(m, width="100%", height=740, key="gis_pure_calc_map_v41")
    drawn_features = map_data.get("all_drawings")

    if input_mode == "🗺️ 地図に描画" and drawn_features:
        last_feature = drawn_features[-1]
        geom_type = last_feature["geometry"]["type"]
        user_geom = shape(last_feature["geometry"])
        has_data = True
        
        user_gdf = gpd.GeoDataFrame(geometry=[user_geom], crs="EPSG:4326")
        
        if geom_type == "Point":
            site_area = 0.0
            user_gdf_m = user_gdf.to_crs(epsg=6676)
            buffer_geom_m = user_gdf_m.geometry.iloc[0].buffer(50.0)
            buffer_gdf = gpd.GeoDataFrame(geometry=[buffer_geom_m], crs="EPSG:6676")
            buffer_gdf_4326 = buffer_gdf.to_crs(epsg=4326)
            search_poly = buffer_gdf_4326.geometry.iloc[0]
        else:
            site_area = calculate_area_m2(user_geom)
            search_poly = user_geom

        # --- 区域区分判定 ---
        if gdf_shigaika is not None and gdf_chousei is not None:
            if geom_type == "Point":
                in_shigaika = gdf_shigaika.contains(user_geom).any()
                in_chousei = gdf_chousei.contains(user_geom).any()
                if in_shigaika: current_zone = "市街化区域"
                elif in_chousei: current_zone = "市街化調整区域"
                else: current_zone = "都市計画区域外"
            else:
                inter_shigaika = gpd.overlay(user_gdf, gdf_shigaika, how='intersection')
                shigaika_area = inter_shigaika.geometry.map(calculate_area_m2).sum() if not inter_shigaika.empty else 0.0
                inter_chousei = gpd.overlay(user_gdf, gdf_chousei, how='intersection')
                chousei_area = inter_chousei.geometry.map(calculate_area_m2).sum() if not inter_chousei.empty else 0.0
                if shigaika_area >= site_area * 0.99: current_zone = "市街化区域"
                elif chousei_area >= site_area * 0.99: current_zone = "市街化調整区域"
                else: current_zone = "混在区域（要確認）"

        # --- 用途地域・建蔽率・容積率判定 ---
        if gdf_use is not None:
            possible_use = gdf_use.iloc[list(gdf_use.sindex.intersection(user_geom.bounds))]
            if not possible_use.empty:
                if geom_type == "Point":
                    match_use = possible_use[possible_use.contains(user_geom)]
                    if not match_use.empty:
                        row = match_use.iloc[0]
                        use_choice = row.get("A29_005", "指定なし")
                        k_val = row.get("A29_006")
                        y_val = row.get("A29_007")
                        
                        # 値が存在すれば整形、なければ「指定なし」
                        kinpei_str = f"{int(float(k_val))}%" if pd.notna(k_val) and str(k_val).strip() != "" else "指定なし"
                        youseki_str = f"{int(float(y_val))}%" if pd.notna(y_val) and str(y_val).strip() != "" else "指定なし"
                    else:
                        use_choice = "指定なし"
                        kinpei_str = "指定なし"
                        youseki_str = "指定なし"
                else:
                    inter_use = gpd.overlay(user_gdf, possible_use, how='intersection')
                    if not inter_use.empty:
                        inter_use["calc_area"] = [calculate_area_m2(g) for g in inter_use.geometry]
                        
                        area_summary = inter_use.groupby(["A29_005", "A29_006", "A29_007"], dropna=False)["calc_area"].sum()
                        max_idx = area_summary.idxmax()
                        
                        if area_summary.max() >= site_area * 0.99:
                            use_choice = max_idx[0]
                            kinpei_str = f"{int(float(max_idx[1]))}%" if pd.notna(max_idx[1]) and str(max_idx[1]).strip() != "" else "指定なし"
                            youseki_str = f"{int(float(max_idx[2]))}%" if pd.notna(max_idx[2]) and str(max_idx[2]).strip() != "" else "指定なし"
                        else:
                            distinct_uses = inter_use["A29_005"].unique()
                            use_choice = f"{', '.join(distinct_uses)}（混在）"
                            
                            k_list = [f"{int(float(k))}%" for k in inter_use["A29_006"].dropna().unique() if str(k).strip() != ""]
                            y_list = [f"{int(float(y))}%" for y in inter_use["A29_007"].dropna().unique() if str(y).strip() != ""]
                            kinpei_str = ", ".join(k_list) if k_list else "指定なし"
                            youseki_str = ", ".join(y_list) if y_list else "指定なし"
                    else:
                        use_choice = "指定なし"
                        kinpei_str = "指定なし"
                        youseki_str = "指定なし"
            else:
                use_choice = "指定なし"
                kinpei_str = "指定なし"
                youseki_str = "指定なし"

        # 💡【新規追加】市街化調整区域で建蔽率・容積率が「指定なし」の場合のデフォルト値設定
        # 今後、自治体ごとの分岐（if city_name == "〇〇市": 等）をここに拡張できるようにしています
        if current_zone == "市街化調整区域":
            if kinpei_str == "指定なし":
                kinpei_str = "60%"
            if youseki_str == "指定なし":
                youseki_str = "200%"
        # --- 巴川流域判定 ---
        if gdf_tomoe is not None:
            if geom_type == "Point":
                is_tomoe = gdf_tomoe.contains(user_geom).any()
            else:
                inter_tomoe = gpd.overlay(user_gdf, gdf_tomoe, how='intersection')
                is_tomoe = not inter_tomoe.empty

        # --- 町名マスター判定 ---
        if gdf_towns is not None:
            possible_towns = gdf_towns.iloc[list(gdf_towns.sindex.intersection(user_geom.bounds))]
            if not possible_towns.empty:
                if geom_type == "Point":
                    match_towns = possible_towns[possible_towns.contains(user_geom)]
                else:
                    match_towns = gpd.overlay(user_gdf, possible_towns, how='intersection')
                
                if not match_towns.empty:
                    located_list = [(row.get("CITY_NAME", ""), row.get("S_NAME", "")) for _, row in match_towns.iterrows() if row.get("CITY_NAME") and row.get("S_NAME")]
                    display_towns = [f"{c} {s}" if idx==0 else s for idx, (c, s) in enumerate(list(set(located_list)))]
                    detailed_location = ", ".join(display_towns)
                else: detailed_location = "静岡市（境界外）"
        else: detailed_location = "静岡市"

        # --- 農地法判定 ---
        if gdf_agri is not None:
            possible_agri = gdf_agri.iloc[list(gdf_agri.sindex.intersection(search_poly.bounds))]
            if not possible_agri.empty:
                if geom_type == "Point":
                    agri_near = possible_agri.intersects(search_poly).any()
                else:
                    inter_agri = gpd.overlay(user_gdf, possible_agri, how='intersection')
                    agri_area = sum([calculate_area_m2(g) for g in inter_agri.geometry]) if not inter_agri.empty else 0.0

        # --- 森林法判定 ---
        if gdf_forest is not None:
            possible_forest = gdf_forest.iloc[list(gdf_forest.sindex.intersection(search_poly.bounds))]
            if not possible_forest.empty:
                if geom_type == "Point":
                    forest_near = possible_forest.intersects(search_poly).any()
                else:
                    inter_forest = gpd.overlay(user_gdf, possible_forest, how='intersection')
                    forest_area = sum([calculate_area_m2(g) for g in inter_forest.geometry]) if not inter_forest.empty else 0.0

        # --- 🛠️ 河川情報（ピン・ポリゴン両対応に強化） ---
        if gdf_river is not None:
            user_gdf_m = user_gdf.to_crs(epsg=6676)
            gdf_river_m = gdf_river.to_crs(epsg=6676)
            
            # 1km（1000m）バッファで周囲の河川インデックスを検索
            possible_river = gdf_river_m.iloc[list(gdf_river_m.sindex.intersection(user_gdf_m.geometry.iloc[0].buffer(1000).bounds))]
            if not possible_river.empty:
                distances = possible_river.distance(user_gdf_m.geometry.iloc[0])
                shortest_dist = int(round(distances.min()))
                
                if shortest_dist < 1000:
                    min_idx = distances.idxmin()
                    nearest_river_dist = shortest_dist
                    has_river_dist = True
                    r_name = possible_river.loc[min_idx, 'W05_004']
                    nearest_river_name = r_name if pd.notna(r_name) else "名称不明の河川"
                    r_class = possible_river.loc[min_idx, 'W05_003']
                    nearest_river_class = "一級河川" if str(r_class).strip() in ['1','2','5','6'] else "二級河川" if str(r_class).strip() in ['3','7'] else "準用・普通河川等"
                else:
                    has_river_dist = False
            else:
                has_river_dist = False

        # --- 洪水浸水想定 ---
        if gdf_flood is not None:
            possible_flood = gdf_flood.iloc[list(gdf_flood.sindex.intersection(user_geom.bounds))]
            if not possible_flood.empty:
                if geom_type == "Point":
                    match_flood = possible_flood[possible_flood.contains(user_geom)]
                else:
                    match_flood = gpd.overlay(user_gdf, possible_flood, how='intersection')
                
                if not match_flood.empty:
                    flood_hit = True
                    match_flood['A31a_205_num'] = pd.to_numeric(match_flood['A31a_205'], errors='coerce').fillna(0).astype(int)
                    max_row = match_flood.loc[match_flood['A31a_205_num'].idxmax()]
                    flood_river_name = max_row.get('A31a_202', '名称未定の河川')
                    flood_rank_code = str(max_row.get('A31a_205', ''))
                    rank_desc = {"1":"0.5m未満", "2":"0.5m〜3.0m未満", "3":"3.0m〜5.0m未満", "4":"5.0m〜10.0m未満", "5":"10.0m〜20.0m未満", "6":"20.0m以上"}
                    flood_desc = rank_desc.get(flood_rank_code, "（要窓口確認）")

        # --- 土砂災害警戒区域判定 ---
        if gdf_dosha is not None:
            possible_dosha = gdf_dosha.iloc[list(gdf_dosha.sindex.intersection(search_poly.bounds))]
            if not possible_dosha.empty:
                if geom_type == "Point":
                    dosha_near = possible_dosha.intersects(search_poly).any()
                else:
                    inter_dosha = gpd.overlay(user_gdf, possible_dosha, how='intersection')
                    if not inter_dosha.empty:
                        dosha_hit = True
                        inter_dosha['calc_area'] = inter_dosha.geometry.map(calculate_area_m2)
                        inter_dosha['A33_002_str'] = inter_dosha['A33_002'].astype(str).str.strip()
                        dosha_yellow_area = inter_dosha[inter_dosha['A33_002_str'] == '1']['calc_area'].sum()
                        dosha_red_area = inter_dosha[inter_dosha['A33_002_str'] == '2']['calc_area'].sum()

        # 🚧 都市計画道路の空間判定ロジック（新規追加）
        if gdf_road is not None:
            possible_road = gdf_road.iloc[list(gdf_road.sindex.intersection(search_poly.bounds))]
            if not possible_road.empty:
                if geom_type == "Point":
                    road_near = possible_road.intersects(search_poly).any()
                else:
                    inter_road = gpd.overlay(user_gdf, possible_road, how='intersection')
                    road_area = sum([calculate_area_m2(g) for g in inter_road.geometry]) if not inter_road.empty else 0.0

        # 🏺 埋蔵文化財包蔵地の空間判定ロジック（新規追加）
        if gdf_cultural is not None:
            possible_cultural = gdf_cultural.iloc[list(gdf_cultural.sindex.intersection(search_poly.bounds))]
            if not possible_cultural.empty:
                if geom_type == "Point":
                    cultural_near = possible_cultural.intersects(search_poly).any()
                else:
                    inter_cultural = gpd.overlay(user_gdf, possible_cultural, how='intersection')
                    cultural_area = sum([calculate_area_m2(g) for g in inter_cultural.geometry]) if not inter_cultural.empty else 0.0

        # --- 用途地域判定 ---
        if gdf_use is not None:
            possible_use = gdf_use.iloc[list(gdf_use.sindex.intersection(user_geom.bounds))]
            if not possible_use.empty:
                if geom_type == "Point":
                    match_use = possible_use[possible_use.contains(user_geom)]
                    use_choice = match_use.iloc[0].get("A29_005", "指定なし") if not match_use.empty else "指定なし"
                else:
                    inter_use = gpd.overlay(user_gdf, possible_use, how='intersection')
                    if not inter_use.empty:
                        inter_use["calc_area"] = [calculate_area_m2(g) for g in inter_use.geometry]
                        area_summary = inter_use.groupby("A29_005")["calc_area"].sum()
                        use_choice = area_summary.idxmax() if area_summary.max() >= site_area * 0.99 else f"{', '.join(area_summary.index)}（混在）"
                    else: use_choice = "指定なし"

    use_district = "others"
    if "準工業" in use_choice: use_district = "quasi_industrial"
    elif "工業" in use_choice or "工業専用" in use_choice: use_district = "industrial"

    if has_data:
        dev_limit = 1000.0 if current_zone == "市街化区域" else 500.0
        is_dev_required = (site_area >= dev_limit) or (current_zone == "市街化調整区域" and selected_purpose is not None and selected_purpose["cat"] in ["building", "spec_1"])
        morido_required = morido_area > 500 or kiri_height > 2.0 or mori_height > 1.0 or ((kiri_height + mori_height) > 2.0 and kiri_height > 0 and mori_height > 0)
        
        vol_min, vol_max, max_basis, max_green = 0.0, 0.0, "免除", 0.0
        if geom_type != "Point":
            vol_min, vol_max = (site_area * 0.11, site_area * 0.13) if is_tomoe else (site_area * 0.06, site_area * 0.08)
            green_reqs = {"静岡市みどり条例（敷地面積の5%）": site_area * 0.05}
            if selected_purpose is not None and selected_purpose["is_factory_law"] and (site_area >= 9000 or building_area >= 3000):
                r_green = 0.05 if use_district == "industrial" else 0.10 if use_district == "quasi_industrial" else 0.20
                green_reqs["工場立地法"] = site_area * r_green
            max_basis = max(green_reqs, key=green_reqs.get)
            max_green = green_reqs[max_basis]

        report_data = {
            "input_mode": input_mode, "geom_type": geom_type, "loc_label": detailed_location, "site_area": site_area, "current_zone": current_zone, "target_use_name": use_choice, "kinpei_str": kinpei_str, "youseki_str": youseki_str,
            "is_dev_required": is_dev_required, "agri_area": agri_area, "agri_near": agri_near, "check_morido": check_morido, "morido_required": morido_required, 
            "forest_area": forest_area, "forest_near": forest_near, "is_tomoe": is_tomoe, "vol_min": vol_min, "vol_max": vol_max, "purpose_none": (selected_purpose is None), 
            "max_basis": max_basis, "max_green": max_green, "gdf_flood_none": (gdf_flood is None), "flood_hit": flood_hit, "flood_river_name": flood_river_name, 
            "flood_rank_code": flood_rank_code, "flood_desc": flood_desc, "has_river_dist": has_river_dist, "nearest_river_name": nearest_river_name, 
            "nearest_river_class": nearest_river_class, "nearest_river_dist": nearest_river_dist, "gdf_dosha_none": (gdf_dosha is None), "dosha_hit": dosha_hit, 
            "dosha_red_area": dosha_red_area, "dosha_yellow_area": dosha_yellow_area, "dosha_near": dosha_near,
            "road_area": road_area, "road_near": road_near,
            "cultural_area": cultural_area, "cultural_near": cultural_near,
        }

    with col_btn:
        if has_data:
            if st.button("**判定**", type="primary", use_container_width=True, key="btn_pure_bold_wide"):
                show_result_dialog(report_data)
