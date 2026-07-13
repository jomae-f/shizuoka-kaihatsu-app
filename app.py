import os
import io
import base64
from functools import partial
import streamlit as st
import geopandas as gpd
import pandas as pd
import folium
from streamlit_folium import st_folium
from shapely.geometry import shape, Point
from shapely.ops import transform
import pyproj
from xhtml2pdf import pisa

st.set_page_config(layout="wide")
st.title("静岡市開発行為 要件判定システム")

if "center_lat" not in st.session_state:
    st.session_state.center_lat = 34.975562
if "center_lon" not in st.session_state:
    st.session_state.center_lon = 138.382758

# ----------------------------------------------------
# GISデータの読み込み（高速Feather版）
# ----------------------------------------------------
@st.cache_data
def load_spatial_files():
    try:
        # パス存在チェック付きでの読み込みを共通化
        def load_gdf(path, fix_geom=False):
            if not os.path.exists(path):
                return None
            gdf = gpd.read_feather(path)
            if fix_geom and hasattr(gdf, 'make_valid'):
                gdf['geometry'] = gdf['geometry'].make_valid()
            return gdf

        # 1. 存在チェックが不要な必須データ（直接読み込み）
        gdf_shigaika = gpd.read_feather("data/shigaika.feather")
        gdf_chousei  = gpd.read_feather("data/chousei.feather")
        gdf_tomoe    = gpd.read_feather("data/tomoe.feather")

        # 2. 存在チェックや形状補正が必要なデータ
        gdf_towns    = load_gdf("data/towns.feather")
        gdf_use      = load_gdf("data/use_districts.feather")
        gdf_dosha    = load_gdf("data/dosha_shizuoka.feather", fix_geom=True)
        gdf_agri     = load_gdf("data/agri_shizuoka.feather")
        gdf_flood    = load_gdf("data/flood_max_shizuoka.feather", fix_geom=True)
        gdf_cultural = load_gdf("data/iseki_shizuoka.feather")
        gdf_forest   = load_gdf("data/forest_shizuoka.feather")
        gdf_river    = load_gdf("data/river_shizuoka.feather")
        gdf_road     = load_gdf("data/plan-roads_shizuoka.feather")
            
        return gdf_towns, gdf_shigaika, gdf_chousei, gdf_use, gdf_dosha, gdf_agri, gdf_flood, gdf_cultural, gdf_forest, gdf_tomoe, gdf_river, gdf_road
    except Exception as e:
        st.error(f"高速データの読み込み失敗: {e}")
        return (None,) * 12

# ----------------------------------------------------
# 町名マスターの読み込みと50音分類
# ----------------------------------------------------
@st.cache_data
def load_town_master():
    df = pd.read_csv("townname_shizuoka.csv", encoding="utf-8")
    
    def get_kana_group_strict(kana):
        if not isinstance(kana, str) or len(kana) == 0: return "その他"
        c = kana[0]
        if c in "あいうえお": return "あ行"
        if c in "かきくけこがぎぐげご": return "か行"
        if c in "さしすせそざじずぜぞ": return "さ行"
        if c in "たちつてとだじづでどっ": return "た行"
        if c in "なにぬねの": return "な行"
        if c in "はひふへほばびぶべぼぱぴぷぺぽ": return "は行"
        if c in "まみむめも": return "ま行"
        if c in "やゆよゃゅょ": return "や行"
        if c in "らりるれろ": return "ら行"
        if c in "わをん": return "わ行"
        return "undefined"

    df["50音分類"] = df["ふりがな"].apply(get_kana_group_strict)
    return df


def on_town_change():
    """手入力で町名が変更されたときに、地図の中心座標を自動更新するコールバック"""
    if "selected_town_name" in st.session_state and st.session_state.selected_town_name:
        town_name = st.session_state.selected_town_name
        try:
            town_data = gdf_towns[gdf_towns["S_NAME"] == town_name]
            
            if not town_data.empty:
                centroid = town_data.iloc[0].geometry.centroid
                st.session_state.center_lat = float(centroid.y)
                st.session_state.center_lon = float(centroid.x)
            else:
                st.toast(f"⚠️ 空間データの S_NAME に『{town_name}』が見つかりませんでした。")
        except Exception as e:
            st.toast(f"❌ 座標取得エラー: {e}")

# データの初期ロード実行
gdf_towns, gdf_shigaika, gdf_chousei, gdf_use, gdf_dosha, gdf_agri, gdf_flood, gdf_cultural, gdf_forest, gdf_tomoe, gdf_river, gdf_road = load_spatial_files()

# ----------------------------------------------------
# 面積計算（世界測地系4326 から 平面直角座標系6676 へ投影変換）
# ----------------------------------------------------
def calculate_area_m2(geom):
    project = partial(pyproj.transform, pyproj.Proj(init='epsg:4326'), pyproj.Proj(init='epsg:6676'))
    return transform(project, geom).area

# ----------------------------------------------------
# 📄 xhtml2pdf：A4レイアウト（PDF出力処理）
# ----------------------------------------------------
def generate_pdf(report_data):
    import io
    import os
    import math
    from xhtml2pdf import pisa
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    # 1. カラー判定ロジック
    def get_custom_color(label_name, status_text):
        if not status_text:
            return "#ffffff"
        
        if label_name == "周辺の道路": return "#c8e6c9"
        if label_name == "周辺の河川": return "#bbdefb"
        if label_name == "緑地": return "#ffffff" if "不要" in status_text else "#ffe0b2"
        if label_name == "緩衝帯": return "#ffe0b2" if any(k in status_text for k in ["必要", "m以上"]) else "#ffffff"
        if label_name == "調整池": return "#ffffff" if "不要" in status_text or status_text in ["免除", "―"] else "#bbdefb"
        if label_name == "洪水浸水想定区域": return "#c8e6c9" if any(k in status_text for k in ["区域外", "なし"]) else "#ffcdd2"

        if label_name == "土砂災害警戒区域":
            if status_text == "レッド": return "#ffcdd2"
            if any(k in status_text for k in ["イエロー", "50m以内"]): return "#fff9c4"
            return "#c8e6c9" if "区域外" in status_text else "#ffffff"

        if label_name in ["農地法", "埋蔵文化財", "森林法"]:
            if "50m以内" in status_text: return "#fff9c4"
            kw_map = {"農地法": ("農地あり", "農地なし"), "埋蔵文化財": ("遺跡あり", "遺跡なし"), "森林法": ("森林あり", "森林なし")}
            kw_red, kw_green = kw_map[label_name]
            if kw_red in status_text: return "#ffcdd2"
            if kw_green in status_text: return "#c8e6c9"

        if any(k in status_text for k in ["必要", "制限", "レッド", "危険", "区域内"]): return "#ffcdd2"
        if any(k in status_text for k in ["注意", "確認", "イエロー", "要相談", "協議", "10m以内に区域"]): return "#fff9c4"
        if any(k in status_text for k in ["不要", "許可不要", "免除", "該当なし", "区域外", "なし"]): return "#c8e6c9"
        return "#ffffff"

    # 2. フォントの登録
    font_ttc_path = os.path.join(".", "fonts", "YuGothB.ttc")
    target_font = "YuGothic" if os.path.exists(font_ttc_path) else "HeiseiKakuGo-W5"
    if target_font == "YuGothic":
        try:
            pdfmetrics.registerFont(TTFont('YuGothic', font_ttc_path, index=0))
        except Exception:
            target_font = "HeiseiKakuGo-W5"

    is_point_mode = report_data.get("geom_type") == "Point"
    input_mode = report_data.get("input_mode", "")
    
    site_area = report_data.get("site_area") or 0.0
    area_text = f"{site_area:,.1f} ㎡" if not is_point_mode else "―"
    
    loc_label = report_data.get('loc_label') or '―'
    current_zone = report_data.get('current_zone') or '―'
    
    target_use_name = report_data.get('target_use_name') or '―'
    combined_spec_str = report_data.get('combined_spec_str') or '―'

    # 3. 主要法令に基づく手続要件のデータ成形
    toshi_status = "―" if is_point_mode else ("必要" if report_data.get("is_dev_required") else "不要")
    agri_status = report_data.get("agri_point_status") or "―"
    forest_status = report_data.get("forest_point_status") or "―"
    road_status = report_data.get("road_status") or "―"
    cultural_status = report_data.get("cultural_point_status") or "―"
    
    flood_status = report_data.get("flood_status") or "―"
    river_status = report_data.get("river_dist_status") or "―"
    road_display = "―"
    dosha_base = report_data.get("dosha_point_status") or "―"
    dosha_status = dosha_base.replace("イエロー、50m以内にレッド", 'イエロー、<br />50m以内にレッド') if dosha_base != "―" else "―"

    pond_display, green_display, bz_status = "不要", "不要", "不要"
    
    if not is_point_mode:
        pond_text = report_data.get("pond_volume_str") or "―"
        if pond_text and pond_text not in ["不要", "免除", "―"]:
            pond_display = f"{pond_text}<br />（巴川流域）" if report_data.get("is_tomoe") else pond_text
        elif pond_text == "―":
            pond_display = "―"

        max_basis = report_data.get("max_basis") or "不要"
        if "静岡市" in loc_label and site_area >= 1000:
            green_area_val = math.ceil((report_data.get("max_green") or 0.0) * 100) / 100
            
            if green_area_val > 0.0:
                green_display = f"{green_area_val:,.2f} ㎡以上<br />（{max_basis}）"
            else:
                green_display = "不要"
        else:
            green_display = "不要"

        bz_status = report_data.get("buffer_zone_status") or "不要"

    # 4. HTMLテーブルの組み立て
    if input_mode == "✍️ 手入力":
        table_body_html = f"""
            <tr>
                <th style="background-color: {get_custom_color('開発許可', toshi_status)};">開発許可</th>
                <td style="background-color: {get_custom_color('開発許可', toshi_status)};">{toshi_status}</td>
                <th style="background-color: {get_custom_color('緑地', green_display)};">緑地</th>
                <td style="background-color: {get_custom_color('緑地', green_display)};">{green_display}</td>
            </tr>
            <tr>
                <th style="background-color: {get_custom_color('調整池', pond_display)};">調整池</th>
                <td style="background-color: {get_custom_color('調整池', pond_display)};">{pond_display}</td>
                <th style="background-color: {get_custom_color('緩衝帯', bz_status)};">緩衝帯</th>
                <td style="background-color: {get_custom_color('緩衝帯', bz_status)};">{bz_status}</td>
            </tr>
        """
    else:
        table_body_html = f"""
            <tr>
                <th style="background-color: {get_custom_color('開発許可', toshi_status)};">開発許可</th>
                <td style="background-color: {get_custom_color('開発許可', toshi_status)};">{toshi_status}</td>
                <th style="background-color: {get_custom_color('都市計画道路', road_status)};">都市計画道路</th>
                <td style="background-color: {get_custom_color('都市計画道路', road_status)};">{road_status}</td>
            </tr>
            <tr>
                <th style="background-color: {get_custom_color('土砂災害警戒区域', dosha_status)};">土砂災害警戒区域</th>
                <td style="background-color: {get_custom_color('土砂災害警戒区域', dosha_status)};">{dosha_status}</td>
                <th style="background-color: {get_custom_color('周辺の道路', road_display)};">周辺の道路</th>
                <td style="background-color: {get_custom_color('周辺の道路', road_display)};">{road_display}</td>
            </tr>
            <tr>
                <th style="background-color: {get_custom_color('農地法', agri_status)};">農地法</th>
                <td style="background-color: {get_custom_color('農地法', agri_status)};">{agri_status}</td>
                <th style="background-color: {get_custom_color('緑地', green_display)};">緑地</th>
                <td style="background-color: {get_custom_color('緑地', green_display)};">{green_display}</td>
            </tr>
            <tr>
                <th style="background-color: {get_custom_color('洪水浸水想定区域', flood_status)};">洪水浸水想定区域</th>
                <td style="background-color: {get_custom_color('洪水浸水想定区域', flood_status)};">{flood_status}</td>
                <th style="background-color: {get_custom_color('緩衝帯', bz_status)};">緩衝帯</th>
                <td style="background-color: {get_custom_color('緩衝帯', bz_status)};">{bz_status}</td>
            </tr>
            <tr>
                <th style="background-color: {get_custom_color('埋蔵文化財', cultural_status)};">埋蔵文化財</th>
                <td style="background-color: {get_custom_color('埋蔵文化財', cultural_status)};">{cultural_status}</td>
                <th style="background-color: {get_custom_color('調整池', pond_display)};">調整池</th>
                <td style="background-color: {get_custom_color('調整池', pond_display)};">{pond_display}</td>
            </tr>
            <tr>
                <th style="background-color: {get_custom_color('森林法', forest_status)};">森林法</th>
                <td style="background-color: {get_custom_color('森林法', forest_status)};">{forest_status}</td>
                <th style="background-color: {get_custom_color('周辺の河川', river_status)};">周辺の河川</th>
                <td style="background-color: {get_custom_color('周辺の河川', river_status)};">{river_status}</td>
            </tr>
        """

    # 5. HTML・CSSテンプレート組み立て
    html_content = f"""
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            @page {{ size: a4; margin: 0.8cm; }}
            body {{ font-family: "{target_font}", sans-serif; color: #121212; font-size: 10.5pt; line-height: 1.4; }}
            .header {{ border-bottom: 2px solid #003366; padding-bottom: 8px; margin-bottom: 20px; }}
            .title {{ font-size: 18pt; font-weight: bold; color: #003366; }}
            table.meta-table {{ width: 100%; border-collapse: collapse; margin-bottom: 20px; }}
            table.meta-table td {{ padding: 8px 12px; border: 1px solid #555555; vertical-align: middle; }}
            table.meta-table .meta-label {{ color: #121212; font-weight: bold; font-size: 11pt; width: 25%; background-color: #d1d1d1; text-align: center !important; vertical-align: middle; padding: 8px 0px !important; }}
            table.main-table {{ width: 100%; border-collapse: collapse; margin-top: 10px; margin-bottom: 15px; }}
            table.main-table th, table.main-table td {{ border: 1px solid #555555; font-size: 9.5pt; vertical-align: middle; }}
            table.main-table th {{ font-weight: bold; text-align: center; padding: 15px 10px; }}
            table.main-table td {{ width: 30%; text-align: left; padding: 10px 10px 10px 15px; }}
            .footer {{ text-align: center; font-size: 8pt; color: #121212; margin-top: 40px; padding-top: 10px; }}
            .footer-title {{ display: block; font-weight: bold; color: #121212; margin: 0 0 4px 0 !important; }}
            .footer-line {{ display: block; margin: 0 !important; padding: 1px 0 !important; line-height: 1.4; }}
        </style>
    </head>
    <body>
        <div class="header"><div class="title">開発行為 判定結果レポート</div></div>
        <table class="meta-table">
            <tr><td class="meta-label">敷地所在</td><td><strong>{loc_label}</strong></td></tr>
            <tr><td class="meta-label">敷地面積</td><td><strong>{area_text}</strong></td></tr>
            <tr><td class="meta-label">区域区分</td><td><strong>{current_zone}</strong></td></tr>
            <tr><td class="meta-label">用途地域</td><td><strong>{target_use_name}</strong></td></tr>
            <tr><td class="meta-label">建蔽率/容積率</td><td><strong>{combined_spec_str}</strong></td></tr>
        </table>
        <table class="main-table">
            {table_body_html}  </table>
        <div class="footer">
            <div class="footer-title">静岡市開発行為 要件判定システム</div>
            <div class="footer-line">本レポートはGISデータに基づく簡易判定結果であり、実際の状況や最新の指定内容とは異なる場合があります。</div>
            <div class="footer-line">実務に際しては必ず各種データの出典元情報や、各関係官庁の担当窓口にて最新の法令・要件をご確認ください。</div>
        </div>
    </body>
    </html>
    """

    # 6. xhtml2pdfによるPDF変換処理とバイナリ出力
    pdf_buffer = io.BytesIO()
    pisa_status = pisa.CreatePDF(io.BytesIO(html_content.encode("utf-8")), dest=pdf_buffer, encoding='utf-8')
    if pisa_status.err: 
        raise Exception("HTMLからPDFへの変換処理でエラーが発生しました。")
    return pdf_buffer.getvalue()

# ----------------------------------------------------
# 💡 ポップアップ（ダイアログ）の定義
# ----------------------------------------------------
def themes_color_get(theme):
    return {"red": "#c62828", "orange": "#e65100", "yellow": "#f5a623", "blue": "#0288d1", "green": "#2e7d32"}.get(theme, "#555")

@st.dialog("📊 開発要件 判定結果レポート", width="large")
def show_result_dialog(report_data):
    import math

    st.markdown("""
        <style>
        div[data-testid="stDialog"] h2 {
            font-size: 28px !important;
            font-weight: bold !important;
        }
        </style>
    """, unsafe_allow_html=True)

    loc_label = report_data["loc_label"]
    is_point_mode = report_data.get("geom_type") == "Point"
    area_display = f"{report_data['site_area']:,.1f} ㎡" if not is_point_mode else "―"
    lat, lon = report_data.get("center_lat"), report_data.get("center_lon")
    input_mode = report_data.get("input_mode", "")
    
    current_zone = report_data["current_zone"]
    target_use_name = report_data["target_use_name"]
    combined_spec_str = report_data.get("combined_spec_str", "―")

    def make_link_html(url, title, theme=None):
        if lat and lon:
            color = themes_color_get(theme) if theme else "#555"
            return f'<a href="{url}" target="_blank" style="color: {color}; text-decoration: underline;">{title}</a>'
        return title

    zone_title = make_link_html(f"https://city.shizuoka.geocloud.jp/webgis/?z=18&ll={lat:.6f}%2C{lon:.6f}&t=dm&mp=300&op=70&ot=1&vlf=000001" if lat else "", "🌐 区域区分")
    use_title = make_link_html(f"https://city.shizuoka.geocloud.jp/webgis/?z=18&ll={lat:.6f}%2C{lon:.6f}&t=dm&mp=300&op=70&ot=1&vlf=000002" if lat else "", "🏢 用途地域")

    st.markdown(f"""
    <div style="background-color: #f0f2f6; padding: 22px 18px; border-radius: 10px; margin-bottom: 24px; display: flex; justify-content: space-between; gap: 12px; border-left: 6px solid #a3a8b4; align-items: flex-start;">
        <div style="flex: 1.5;">
            <div style="font-size: 1.15rem; color: #555; margin-bottom: 8px; font-weight: bold;">📍 敷地所在</div>
            <div style="font-size: 1.35rem; font-weight: bold; color: #111; line-height: 1.3;">{loc_label}</div>
        </div>
        <div style="flex: 1.0; border-left: 2px solid #cbd5e1; padding-left: 14px;">
            <div style="font-size: 1.15rem; color: #555; margin-bottom: 8px; font-weight: bold;">📐 敷地面積</div>
            <div style="font-size: 1.4rem; font-weight: bold; color: #111; line-height: 1.3;">{area_display}</div>
        </div>
        <div style="flex: 1.0; border-left: 2px solid #cbd5e1; padding-left: 14px;">
            <div style="font-size: 1.15rem; color: #555; margin-bottom: 8px; font-weight: bold;">{zone_title}</div>
            <div style="font-size: 1.35rem; font-weight: bold; color: #111; line-height: 1.3;">{current_zone}</div>
        </div>
        <div style="flex: 1.5; border-left: 2px solid #cbd5e1; padding-left: 14px;">
            <div style="font-size: 1.15rem; color: #555; margin-bottom: 8px; font-weight: bold;">{use_title}</div>
            <div style="font-size: 1.35rem; font-weight: bold; color: #111; line-height: 1.3;">{target_use_name}</div>
        </div>
        <div style="flex: 1.2; border-left: 2px solid #cbd5e1; padding-left: 14px;">
            <div style="font-size: 1.15rem; color: #555; margin-bottom: 8px; font-weight: bold;">📐 建蔽率 / 容積率</div>
            <div style="font-size: 1.4rem; font-weight: bold; color: #111; line-height: 1.3;">{combined_spec_str}</div>
        </div>
    </div>
    """, unsafe_allow_html=True)
    
    try:
        st.download_button(
            label="📄 判定結果レポートをPDFでダウンロード ",
            data=generate_pdf(report_data),
            file_name=f"開発要件判定レポート_{loc_label.replace(', ', '_')}.pdf",
            mime="application/pdf",
            use_container_width=True
        )
    except Exception as e:
        st.error(f"PDF生成コンポーネントの準備に失敗しました: {e}")
        
    st.markdown("---")
    diag_col1, diag_col2 = st.columns(2)
    
    def render_law_card(title_html, status_text, theme="green"):
        themes = {
            "red": ("#ffebee", "#ef5350", "#c62828"),
            "orange": ("#fff3e0", "#ffb74d", "#e65100"),
            "yellow": ("#fffde7", "#ffd600", "#ff6f00"),
            "blue": ("#e8f4f8", "#29b6f6", "#0288d1"),
            "green": ("#e8f5e9", "#66bb6a", "#2e7d32")
        }
        bg, border, text = themes[theme]
        st.markdown(f"""
        <div style="background-color: {bg}; border-left: 5px solid {border}; padding: 16px; border-radius: 4px; margin-bottom: 16px;">
            <div style="font-size: 1.3rem; font-weight: bold; color: {text}; margin-bottom: 8px;">{title_html}</div>
            <div style="display: flex; justify-content: flex-end; align-items: center; color: {text}; font-weight: bold; gap: 8px; line-height: 1.4;">
                <span style="font-size: 1.4rem;">{status_text}</span>
            </div>
        </div>
        """, unsafe_allow_html=True)

    # 左カラム（1列目）の制御
    with diag_col1:
        if not is_point_mode:
            is_dev = report_data.get("is_dev_required", False)
            render_law_card("🚨 【開発許可】" if is_dev else "✅ 【開発許可】", "必要" if is_dev else "不要", "red" if is_dev else "green")

        if input_mode != "✍️ 手入力":
            if report_data.get("gdf_dosha_none"):
                st.caption("ℹ️ 土砂災害警戒区域データが見つかりません。")
            else:
                status = report_data.get("dosha_point_status", "区域外")
                theme = "red" if status == "レッド" else ("yellow" if status != "区域外" else "green")
                title = make_link_html(f"https://city.shizuoka.geocloud.jp/webgis/?z=18&ll={lat:.6f}%2C{lon:.6f}&t=roadmap&mp=100&op=70&vlf=007f80" if lat else "", "【土砂災害警戒区域】", theme)
                render_law_card(f"🚨 {title}", status, theme)
                
            agri_status = report_data.get("agri_point_status", "農地なし")
            theme = "red" if agri_status == "農地あり" else ("yellow" if agri_status == "50m以内に農地" else "green")
            title = make_link_html("https://map.maff.go.jp/", "【農地法】", theme)
            render_law_card(f"🚜 {title}", agri_status, theme)
                
            status = report_data.get("flood_status", "区域外")
            theme = "green" if status == "区域外" else "red"
            title = make_link_html(f"https://city.shizuoka.geocloud.jp/webgis/?z=18&ll={lat:.6f}%2C{lon:.6f}&t=roadmap&mp=101&op=70&ot=1&vlf=0003ffffffffffffffffffffffffff" if lat else "", "【洪水浸水想定区域】", theme)
            render_law_card(f"🌊 {title}", status, theme)

            status = report_data.get("cultural_point_status", "✅ 対象外")
            theme = "red" if ("遺跡あり" in status or "あり" in status) else ("yellow" if "50m以内" in status else "green")
            title = make_link_html(f"https://city.shizuoka.geocloud.jp/webgis/?z=18&ll={lat:.6f}%2C{lon:.6f}&t=roadmap&mp=402&op=70&ot=1&vlf=-1" if lat else "", "【埋蔵文化財】", theme)
            render_law_card(f"🏺 {title}", status, theme)
             
            forest_status = report_data.get("forest_point_status", "森林なし")
            theme = "red" if forest_status == "森林あり" else ("yellow" if forest_status == "50m以内に森林" else "green")
            title = make_link_html(f"https://fcloud.pref.shizuoka.jp/fgis/?version=1.26.0525.a#15/{lat:.5f}/{lon:.5f}" if lat else "", "【森林法】", theme)
            render_law_card(f"🌲 {title}", forest_status, theme)

    # 右カラム（2列目）の制御
    with diag_col2:
        if input_mode != "✍️ 手入力":
            status = report_data.get("road_status", "区域外")
            theme = "green" if status == "区域外" else "red"
            title = make_link_html(f"https://city.shizuoka.geocloud.jp/webgis/?z=18&ll={lat:.6f}%2C{lon:.6f}&t=dm&mp=300&op=70&ot=1&vlf=000010000000" if lat else "", "【都市計画道路】", theme)
            render_law_card(f"🛣️ {title}", status, theme)

            if lat and lon:
                road_url = f"https://www2.wagmap.jp/shizuoka/Map?mid=1&mpx={lon + 0.00321:.6f}&mpy={lat - 0.00328:.6f}&bsw=1200&bsh=800"
                road_title = f'<a href="{road_url}" target="_blank" style="color: #2e7d32; text-decoration: underline;">【周辺の道路】</a>'
                road_text = f'<a href="{road_url}" target="_blank" style="color: #2e7d32; text-decoration: underline; font-size: 1.4rem; font-weight: bold;">🔗静岡市地図情報サービス</a>'
            else:
                road_title, road_text = '【周辺の道路】', '<span style="font-size: 1.4rem; font-weight: bold; color: #2e7d32;">🔗静岡市地図情報サービス</span>'
            render_law_card(f"🚗 {road_title}", road_text, "green")

        # 【緑地】（手入力でも維持）
        if "静岡市" in loc_label and report_data["site_area"] >= 1000:
            basis_text = report_data.get("max_basis", "5%, 市みどり条例")
            green_area_val = math.ceil(report_data.get("max_green", 0.0) * 100) / 100
            
            if green_area_val > 0:
                render_law_card("🌲 【緑地】", f'<span style="font-size: 1.15rem;">（{basis_text}）</span> {green_area_val:,.2f}㎡以上', "orange")
            else:
                render_law_card("🌲 【緑地】", "不要", "orange")
        else:
            render_law_card("🌲 【緑地】", "不要", "orange")

        # 【緩衝帯】（手入力でも維持）
        bz_status = report_data.get("buffer_zone_status", "不要")
        render_law_card("🌳 【緩衝帯】", bz_status, "orange")

        # 【調整池】（手入力でも維持）
        if not is_point_mode:
            left_text = '<span style="font-size: 1.15rem;">（巴川流域）</span> ' if report_data.get("is_tomoe", False) else ""
            render_law_card("💧 【調整池】", f'{left_text}{report_data.get("pond_volume_str", "―")}', "blue")

        # 周辺の河川（手入力モードの場合は非表示）
        if input_mode != "✍️ 手入力":
            r_dist_status = report_data.get("river_dist_status", "1km以内に主要河川なし")
            theme = "blue"
            title = make_link_html(f"https://city.shizuoka.geocloud.jp/webgis/?z=18&ll={lat:.6f}%2C{lon:.6f}&t=roadmap&mp=308&op=70&ot=1&vlf=-1" if lat else "", "【周辺の河川】", theme)
            render_law_card(f"🏞️ {title}", r_dist_status, theme)

# ----------------------------------------------------
# 📐 画面レイアウト（2:8 比率）
# ----------------------------------------------------
col_left, col_center = st.columns([2, 8])

# 📊 基本属性の初期化
site_area = 0.0
city_name = "静岡市"
detailed_location = "未選択"
has_data = False
use_choice = "未確定" 
current_zone = "未確定"
geom_type = "Polygon"
kinpei_str = "未確定"
youseki_str = "未確定"

# 📐 各種法令の面積変数（市街化・調整・巴川・農地・文化財・森林・道路）
shigaika_p, chousei_p, tomoe_area, agri_area, cultural_area, forest_area, road_area = 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0

# 🚨 各種法令の近傍フラグ（土砂・農地・文化財・森林・道路）
dosha_near, agri_near, cultural_near, forest_near, road_near = False, False, False, False, False

# 🚨 土砂災害警戒区域の判定変数
dosha_hit = False
dosha_red_area = 0.0
dosha_yellow_area = 0.0

# 🌊 洪水浸水想定の判定変数
flood_hit = False
flood_river_name = ""
flood_rank_code = ""
flood_desc = ""

# 🏞️ 河川距離の判定変数
min_distance_m = float('inf')
nearest_river_name = "名称不明の河川"
nearest_river_class = ""
has_river_dist = False
nearest_river_dist = None

# 📋 空間判定結果の格納用
report_data = {}

# ====================================================
# 🎛️ 画面左側（比率2）：条件設定
# ====================================================
with col_left:
    st.markdown("""
        <style>
            div[data-testid="stRadio"] div[role="radiogroup"] label p { font-size: 18px !important; }
            div[data-testid="stSelectbox"] label p, div[data-testid="stSelectbox"] div[data-baseweb="select"] div,
            div[data-testid="stNumberInput"] label p, div[data-testid="stNumberInput"] input { font-size: 18px !important; }
        </style>
    """, unsafe_allow_html=True)

    st.subheader("⚙️ 条件設定")
    
    st.markdown('<span style="font-size: 22px; font-weight: bold;">【敷地情報の入力方法】</span>', unsafe_allow_html=True)
    input_mode = st.radio("敷地情報の入力方法", ["🗺️ 地図に描画", "✍️ 手入力"], label_visibility="collapsed")
    st.markdown("---")
    
    # セッション状態およびデフォルト値の定義
    selected_use_zone = "指定なし"

    if input_mode == "✍️ 手入力":
        city_name = st.selectbox("所在", ["静岡市"])
        try:
            df_town = load_town_master()
            col_ward, col_kana = st.columns(2)
            
            with col_ward:
                actual_wards = df_town["区名"].unique()
                ward_list = [w for w in ["葵区", "駿河区", "清水区"] if w in actual_wards] + [w for w in actual_wards if w not in ["葵区", "駿河区", "清水区"]]
                selected_ward = st.selectbox("区", ward_list, index=0)
                
            with col_kana:
                df_ward_filtered = df_town[df_town["区名"] == selected_ward]
                kana_order = {"あ行":1, "か行":2, "さ行":3, "た行":4, "な行":5, "は行":6, "ま行":7, "や行":8, "ら行":9, "わ行":10, "その他":11}
                kana_list = sorted([k for k in df_ward_filtered["50音分類"].unique() if k in kana_order], key=lambda x: kana_order[x])
                selected_kana = st.selectbox("50音", kana_list)
                
            df_town_filtered = df_ward_filtered[df_ward_filtered["50音分類"] == selected_kana]
            town_list = df_town_filtered[["町名", "ふりがな"]].drop_duplicates().sort_values("ふりがな")["町名"].tolist()
            
            selected_town = st.selectbox(
                "町名", 
                town_list, 
                key="selected_town_name", 
                on_change=on_town_change
            )
            detailed_location = f"静岡市{selected_ward}{selected_town}"
            
        except Exception as e:
            st.error(f"町名CSVの読み込みエラー: {e}")
            detailed_location = "静岡市"
            
        site_area = st.number_input("敷地面積 (㎡)", min_value=0.0, value=0.0, step=100.0)
        has_data = site_area > 0
        
        current_zone = st.selectbox("区域区分", ["―", "市街化区域", "市街化調整区域", "都市計画区域外"], index=0)
        
        if current_zone == "市街化区域":
            selected_use_zone = st.selectbox("用途地域", ["準工業・工業・工専以外", "準工業地域", "工業地域・工業専用地域"])
        else:
            selected_use_zone = "指定なし"

    # 🏢 事業目的マスタの定義
    st.markdown('<span style="font-size: 22px; font-weight: bold;">【事業目的の選択】</span>', unsafe_allow_html=True)
    poses = {
        "1":  {"label": "工場（製造業、電気・ガス・熱供給業）", "cat": "building", "is_factory_law": True},
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
        selected_purpose = next(v for v in poses.values() if v["label"] == selected_label)
        is_factory_law = selected_purpose["is_factory_law"]
        if selected_purpose["cat"] == "building":
            building_area = st.number_input("建築面積 (㎡)", min_value=0.0, value=0.0, step=100.0)

    st.markdown("---")

# ====================================================
# 🗺️ 画面中央・右側（比率8）：地図の処理＆空間判定
# ====================================================
with col_center:
    col_title, col_btn = st.columns([80, 20])
    with col_title:
        st.subheader("🗺️ 開発区域の指定")
        
    m = folium.Map(
        location=[st.session_state.center_lat, st.session_state.center_lon], 
        zoom_start=15, 
        max_zoom=21, 
        control_scale=True, 
        tiles=None
    )
    
    m.location = [st.session_state.center_lat, st.session_state.center_lon]
    
    if st.session_state.center_lat != 34.975562 or st.session_state.center_lon != 138.382758:
        m.fit_bounds([
            [st.session_state.center_lat - 0.002, st.session_state.center_lon - 0.002],
            [st.session_state.center_lat + 0.002, st.session_state.center_lon + 0.002]
        ])

    folium.TileLayer(tiles='https://cyberjapandata.gsi.go.jp/xyz/seamlessphoto/{z}/{x}/{y}.jpg', attr='国土地理院', name='国土地理院 航空写真', max_zoom=18).add_to(m)
    folium.TileLayer(tiles='https://mt1.google.com/vt/lyrs=m&x={x}&y={y}&z={z}', attr='Google', name='Google Map', max_zoom=21).add_to(m)
    folium.TileLayer(tiles='https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}', attr='Google', name='Google 航空写真', max_zoom=21).add_to(m)

    from folium.plugins import Draw
    Draw(
        export=False, position='topleft', 
        draw_options={'polyline': False, 'circle': False, 'rectangle': False, 'marker': True, 'circlemarker': False, 'polygon': True}
    ).add_to(m)
    
    clear_script = """
    <style>
    .leaflet-draw-toolbar a {
        width: 44px !important;
        height: 44px !important;
        line-height: 44px !important;
        background-image: none !important;
        font-size: 20px !important;
        text-align: center !important;
        display: flex !important;
        align-items: center !important;
        justify-content: center !important;
        text-decoration: none !important;
    }
    .leaflet-draw-draw-polygon::before { content: "⬡" !important; color: #333; font-weight: bold; }
    .leaflet-draw-draw-marker::before { content: "📍" !important; }
    .leaflet-draw-edit-edit::before { content: "✏️" !important; }
    .leaflet-draw-edit-remove::before { content: "🗑️" !important; }

    .leaflet-control-zoom-in,
    .leaflet-control-zoom-out {
        width: 44px !important;
        height: 44px !important;
        line-height: 44px !important;
        font-size: 22px !important;
        display: flex !important;
        align-items: center !important;
        justify-content: center !important;
        text-align: center !important;
    }

    .leaflet-draw-actions, .leaflet-draw-actions li, .leaflet-draw-actions a { height: 38px !important; }
    .leaflet-draw-actions a { line-height: 38px !important; font-size: 15px !important; font-weight: bold !important; padding: 0 12px !important; }
    </style>

    <script>
    document.addEventListener("DOMContentLoaded", function() {
        var checkExist = setInterval(function() {
            var deleteBtn = document.querySelector('.leaflet-draw-edit-remove');
            if (deleteBtn) {
                clearInterval(checkExist);
                deleteBtn.addEventListener('click', function(e) {
                    e.preventDefault(); e.stopPropagation();
                    var maps = Object.values(window).filter(v => v instanceof L.Map);
                    maps.forEach(function(map) {
                        map.eachLayer(function(layer) {
                            if (layer instanceof L.FeatureGroup && typeof layer.clearLayers === 'function') { layer.clearLayers(); }
                        });
                    });
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

    # 💡 モードに合わせてキーを切り分け、描画モード時のリセットを防止
    map_key = f"gis_map_sync_{st.session_state.center_lat}_{st.session_state.center_lon}" if input_mode == "✍️ 手入力" else "gis_map_draw_fixed_key"

    map_data = st_folium(m, width="100%", height=740, key=map_key)
    drawn_features = map_data.get("all_drawings")

    if input_mode == "🗺️ 地図に描画" and drawn_features:
        last_feature = drawn_features[-1]
        geom_type = last_feature["geometry"]["type"]
        user_geom = shape(last_feature["geometry"])
        has_data = True
        
        user_gdf = gpd.GeoDataFrame(geometry=[user_geom], crs="EPSG:4326")
        user_gdf_m = user_gdf.to_crs(epsg=6676)
        target_geom = user_gdf.geometry.iloc[0]

        buffer_geom_m_50 = user_gdf_m.geometry.iloc[0].buffer(50.0)
        search_poly = gpd.GeoDataFrame(geometry=[buffer_geom_m_50], crs="EPSG:6676").to_crs(epsg=4326).geometry.iloc[0]
        
        road_buffer_geom_m_10 = user_gdf_m.geometry.iloc[0].buffer(10.0)
        road_search_poly = gpd.GeoDataFrame(geometry=[road_buffer_geom_m_10], crs="EPSG:6676").to_crs(epsg=4326).geometry.iloc[0]

        if geom_type == "Point":
            site_area = 0.0
            center_lon = user_geom.x
            center_lat = user_geom.y
        else:
            site_area = calculate_area_m2(user_geom)
            center_lon = user_geom.centroid.x
            center_lat = user_geom.centroid.y

        # --- 町名マスター判定 ---
        if gdf_towns is not None:
            possible_towns = gdf_towns.iloc[list(gdf_towns.sindex.intersection(user_geom.bounds))]
            if not possible_towns.empty:
                match_towns = possible_towns[possible_towns.contains(user_geom)] if geom_type == "Point" else gpd.overlay(user_gdf, possible_towns, how='intersection')
                
                if not match_towns.empty:
                    located_list = [(row.get("CITY_NAME", ""), row.get("S_NAME", "")) for _, row in match_towns.iterrows() if row.get("CITY_NAME") and row.get("S_NAME")]
                    display_towns = [f"{c} {s}" if idx==0 else s for idx, (c, s) in enumerate(list(set(located_list)))]
                    detailed_location = ", ".join(display_towns)
                else:
                    detailed_location = "静岡市（境界外）"
        else:
            detailed_location = "静岡市"

        # --- 区域区分判定 ---
        if gdf_shigaika is not None and gdf_chousei is not None:
            if geom_type == "Point":
                if gdf_shigaika.contains(user_geom).any(): current_zone = "市街化区域"
                elif gdf_chousei.contains(user_geom).any(): current_zone = "市街化調整区域"
                else: current_zone = "都市計画区域外"
            else:
                inter_shigaika = gpd.overlay(user_gdf, gdf_shigaika, how='intersection')
                shigaika_area = inter_shigaika.geometry.map(calculate_area_m2).sum() if not inter_shigaika.empty else 0.0
                inter_chousei = gpd.overlay(user_gdf, gdf_chousei, how='intersection')
                chousei_area = inter_chousei.geometry.map(calculate_area_m2).sum() if not inter_chousei.empty else 0.0
                
                if shigaika_area >= site_area * 0.99:
                    current_zone = "市街化区域"
                elif chousei_area >= site_area * 0.99:
                    current_zone = "市街化調整区域"
                else:
                    zones = []
                    if shigaika_area > 1.0: zones.append("市街化区域")
                    if chousei_area > 1.0:  zones.append("市街化調整区域")
                    if (site_area - (shigaika_area + chousei_area)) > 1.0: zones.append("都市計画区域外")
                    current_zone = "<br>".join(zones) if zones else "都市計画区域外"

        # --- 用途地域・建蔽率・容積率判定 ---
        if gdf_use is not None:
            possible_use = gdf_use.iloc[list(gdf_use.sindex.intersection(user_geom.bounds))]
            if not possible_use.empty:
                if geom_type == "Point":
                    match_use = possible_use[possible_use.contains(user_geom)]
                    if not match_use.empty:
                        row = match_use.iloc[0]
                        use_choice = row.get("A29_005", "指定なし")
                        k_val, y_val = row.get("A29_006"), row.get("A29_007")
                        kinpei_str = f"{int(float(k_val))}%" if pd.notna(k_val) and str(k_val).strip() != "" else "指定なし"
                        youseki_str = f"{int(float(y_val))}%" if pd.notna(y_val) and str(y_val).strip() != "" else "指定なし"
                    else:
                        use_choice = kinpei_str = youseki_str = "指定なし"
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
                            use_choice = "<br>".join(inter_use["A29_005"].unique())
                            k_list = [f"{int(float(k))}%" for k in inter_use["A29_006"].dropna().unique() if str(k).strip() != ""]
                            y_list = [f"{int(float(y))}%" for y in inter_use["A29_007"].dropna().unique() if str(y).strip() != ""]
                            kinpei_str = ", ".join(k_list) if k_list else "指定なし"
                            youseki_str = ", ".join(y_list) if y_list else "指定なし"
                    else:
                        use_choice = kinpei_str = youseki_str = "指定なし"
            else:
                use_choice = kinpei_str = youseki_str = "指定なし"
        else:
            use_choice = kinpei_str = youseki_str = "指定なし"

        if current_zone == "市街化調整区域":
            if kinpei_str == "指定なし": kinpei_str = "60%"
            if youseki_str == "指定なし": youseki_str = "200%"

        # --- 開発許可・工場立地法判定 ---
        use_district = "others"
        if "準工業" in use_choice: use_district = "quasi_industrial"
        elif "工業" in use_choice or "工業専用" in use_choice: use_district = "industrial"

        if "市街化区域" in str(current_zone):
            dev_limit = 1000.0
        elif "市街化調整区域" in str(current_zone):
            dev_limit = 500.0
        elif "都市計画区域外" in str(current_zone):
            dev_limit = 10000.0
        else:
            dev_limit = 1000.0

        is_dev_required = (site_area >= dev_limit) or (current_zone == "市街化調整区域" and selected_purpose is not None and selected_purpose["cat"] in ["building", "spec_1"])

        # --- 土砂災害警戒区域判定 ---
        dosha_point_status, dosha_near, dosha_hit = "区域外", False, False
        dosha_yellow_area = dosha_red_area = 0.0      

        if gdf_dosha is not None:
            possible_dosha = gdf_dosha.iloc[list(gdf_dosha.sindex.intersection(search_poly.bounds))]
            if not possible_dosha.empty:
                hit_dosha_near = possible_dosha[possible_dosha.intersects(search_poly)].copy()
                
                if not hit_dosha_near.empty:
                    hit_dosha_near['A33_002_str'] = hit_dosha_near['A33_002'].astype(str).str.strip()
                    dosha_near = True
                    direct_hits = hit_dosha_near[hit_dosha_near.intersects(target_geom)].copy()
                    
                    if not direct_hits.empty:
                        dosha_hit = True
                        if (direct_hits['A33_002_str'] == '2').any():
                            dosha_point_status = "レッド"
                        elif (direct_hits['A33_002_str'] == '1').any():
                            dosha_point_status = "イエロー、50m以内にレッド" if (hit_dosha_near['A33_002_str'] == '2').any() else "イエロー"
                        
                        if geom_type != "Point":
                            inter_dosha = gpd.overlay(user_gdf, direct_hits, how='intersection')
                            if not inter_dosha.empty:
                                inter_dosha['calc_area'] = inter_dosha.geometry.map(calculate_area_m2)
                                inter_dosha['A33_002_str'] = inter_dosha['A33_002'].astype(str).str.strip()
                                dosha_yellow_area = inter_dosha[inter_dosha['A33_002_str'] == '1']['calc_area'].sum()
                                dosha_red_area = inter_dosha[inter_dosha['A33_002_str'] == '2']['calc_area'].sum()
                    else:
                        if (hit_dosha_near['A33_002_str'] == '2').any(): dosha_point_status = "50m以内にレッド"
                        elif (hit_dosha_near['A33_002_str'] == '1').any(): dosha_point_status = "50m以内にイエロー"

        # --- 農地法判定 ---
        agri_point_status = "農地なし"
        if gdf_agri is not None:
            possible_agri = gdf_agri.iloc[list(gdf_agri.sindex.intersection(search_poly.bounds))]
            if not possible_agri.empty:
                hit_agri_near = possible_agri[possible_agri.intersects(search_poly)]
                if not hit_agri_near.empty:
                    agri_point_status = "農地あり" if not hit_agri_near[hit_agri_near.intersects(target_geom)].empty else "50m以内に農地"

        # --- 洪水浸水想定区域判定 ---
        flood_hit = False
        flood_river_name = flood_rank_code = flood_desc = ""

        if gdf_flood is not None:
            possible_flood = gdf_flood.iloc[list(gdf_flood.sindex.intersection(user_geom.bounds))]
            if not possible_flood.empty:
                match_flood = possible_flood[possible_flood.contains(user_geom)] if geom_type == "Point" else gpd.overlay(user_gdf, possible_flood, how='intersection')
                
                if not match_flood.empty:
                    flood_hit = True
                    match_flood['A31a_205_num'] = pd.to_numeric(match_flood['A31a_205'], errors='coerce').fillna(0).astype(int)
                    max_row = match_flood.loc[match_flood['A31a_205_num'].idxmax()]
                    flood_river_name = max_row.get('A31a_202', '名称未定の河川')
                    flood_rank_code = str(max_row.get('A31a_205', ''))
                    
                    rank_desc = {"1":"0.5m未満", "2":"0.5m〜3.0m未満", "3":"3.0m〜5.0m未満", "4":"5.0m〜10.0m未満", "5":"10.0m〜20.0m未満", "6":"20.0m以上"}
                    flood_desc = rank_desc.get(flood_rank_code, "（要窓口確認）")

        flood_status = str(flood_desc).replace("未満", "").strip() if flood_hit and flood_desc else "区域外"

        # --- 埋蔵文化財判定 ---
        cultural_point_status = "遺跡なし"
        if gdf_cultural is not None:
            possible_cultural = gdf_cultural.iloc[list(gdf_cultural.sindex.intersection(search_poly.bounds))]
            if not possible_cultural.empty:
                hit_cultural_near = possible_cultural[possible_cultural.intersects(search_poly)]
                if not hit_cultural_near.empty:
                    cultural_point_status = "遺跡あり" if not hit_cultural_near[hit_cultural_near.intersects(target_geom)].empty else "50m以内に遺跡"

        # --- 森林法判定 ---
        forest_point_status = "森林なし"
        if gdf_forest is not None:
            possible_forest = gdf_forest.iloc[list(gdf_forest.sindex.intersection(search_poly.bounds))]
            if not possible_forest.empty:
                hit_forest_near = possible_forest[possible_forest.intersects(search_poly)]
                if not hit_forest_near.empty:
                    forest_point_status = "森林あり" if not hit_forest_near[hit_forest_near.intersects(target_geom)].empty else "50m以内に森林"

        # --- 緑地・緩衝帯判定 ---
        max_basis, max_green = "不要", 0.0
        if geom_type != "Point":
            green_reqs = {"5%, 市みどり条例": site_area * 0.05}
            if selected_purpose is not None and selected_purpose["is_factory_law"] and (site_area >= 9000 or building_area >= 3000):
                
                use_str = str(use_choice)
                if "準工業" in use_str:
                    r_green = 0.10
                    label = "10%+5%, 工場立地法"
                elif "工業専用" in use_str or "工業地域" in use_str:
                    r_green = 0.05
                    label = "5%+5%, 工場立地法"
                else:  
                    r_green = 0.20
                    label = "20%+5%, 工場立地法"
                
                total_rate = r_green + 0.05
                green_reqs[label] = site_area * total_rate
            
            max_basis = max(green_reqs, key=green_reqs.get)
            max_green = green_reqs[max_basis]

        buffer_zone_status = "不要"
        area_ha = site_area / 10000.0 if geom_type != "Point" else 0.0
        if area_ha >= 1.0:
            buffer_zone_status = "4m以上" if area_ha < 1.5 else "5m以上" if area_ha < 5.0 else "10m以上" if area_ha < 15.0 else "15m以上" if area_ha < 25.0 else "20m以上"

        # --- 巴川流域判定 ---
        if gdf_tomoe is not None:
            is_tomoe = gdf_tomoe.contains(user_geom).any() if geom_type == "Point" else not gpd.overlay(user_gdf, gdf_tomoe, how='intersection').empty
        else:
            is_tomoe = False

        # --- 📐 調整池概算容量計算 ---
        vol_min = vol_max = 0.0
        if site_area < 1000.0:
            pond_volume_str = "不要"
        else:
            A1 = site_area / 10000.0
            alpha = 2 if A1 >= 2.0 else 1
            pond_volume_base = (122 * 0.9 - (28 / 2) * 0.6) * alpha * 30 * 60 * A1 * (1 / 360)
            import math
            
            if is_tomoe:
                factor = 1.1 if A1 <= 0.1 else 1.3 if A1 >= 1.5 else 1.1 + ((A1 - 0.1) * (0.2 / 1.4))
                vol_min = math.ceil(pond_volume_base / 10) * 10
                vol_max = math.ceil((pond_volume_base * factor) / 10) * 10
                pond_volume_str = f"{vol_min:,} ～ {vol_max:,}㎥"
            else:
                pond_volume_rounded = math.ceil(pond_volume_base / 10) * 10
                pond_volume_str = f"{pond_volume_rounded:,}㎥"
                vol_min = vol_max = pond_volume_rounded

        # --- 🏞️ 河川距離判定 ---
        river_dist_status = "1km以内に主要河川なし"
        has_river_dist, nearest_river_name, nearest_river_dist = False, "名称不明の河川", 0

        if gdf_river is not None:
            possible_river = gdf_river.to_crs(epsg=6676).iloc[list(gdf_river.to_crs(epsg=6676).sindex.intersection(user_gdf_m.geometry.iloc[0].buffer(1000).bounds))]
            if not possible_river.empty:
                distances = possible_river.distance(user_gdf_m.geometry.iloc[0])
                shortest_dist = int(round(distances.min()))
                
                if shortest_dist < 1000:
                    min_idx = distances.idxmin()
                    nearest_river_dist = shortest_dist
                    has_river_dist = True
                    r_name = possible_river.loc[min_idx, 'W05_004']
                    nearest_river_name = r_name if pd.notna(r_name) else "名称不明の河川"
                    
                    river_dist_status = f"{nearest_river_name}まで 約 {int(round(nearest_river_dist, -1)):,}m"

        # --- 🛣️ 都市計画道路判定 ---
        road_status = "区域外"
        if gdf_road is not None:
            possible_road = gdf_road.iloc[list(gdf_road.sindex.intersection(road_search_poly.bounds))]
            if not possible_road.empty:
                hit_road_near = possible_road[possible_road.intersects(road_search_poly)]
                if not hit_road_near.empty:
                    road_status = "区域内" if not hit_road_near[hit_road_near.intersects(target_geom)].empty else "10m以内に区域"

        # --- 地図データオブジェクトの生成 ---
        report_data = {
            "input_mode": input_mode, "geom_type": geom_type, "center_lat": center_lat, "center_lon": center_lon,
            "loc_label": detailed_location, "site_area": site_area, "current_zone": current_zone,
            "target_use_name": use_choice, "combined_spec_str": "―", "kinpei_str": kinpei_str, "youseki_str": youseki_str,
            "is_dev_required": is_dev_required, "dosha_point_status": dosha_point_status, "agri_point_status": agri_point_status,
            "pond_volume_str": pond_volume_str, "flood_status": flood_status, "cultural_point_status": cultural_point_status,
            "forest_point_status": forest_point_status, "buffer_zone_status": buffer_zone_status, "river_dist_status": river_dist_status,
            "road_status": road_status, "gdf_dosha_none": (gdf_dosha is None), "is_tomoe": is_tomoe, "vol_min": vol_min, "vol_max": vol_max,
            "purpose_none": (selected_purpose is None), "max_basis": max_basis, "max_green": max_green, "dosha_red_area": dosha_red_area, "dosha_yellow_area": dosha_yellow_area
        }

        k_list = [x.strip() for x in report_data.get('kinpei_str', '').split(',') if x.strip()]
        y_list = [x.strip() for x in report_data.get('youseki_str', '').split(',') if x.strip()]

        if k_list and y_list:
            if len(y_list) == 1 and len(k_list) > 1: y_list = y_list * len(k_list)
            elif len(k_list) == 1 and len(y_list) > 1: k_list = k_list * len(y_list)
                
            spec_pairs = []
            for k, y in zip(k_list, y_list):
                k_val = k if "%" in k or k == "指定なし" else f"{k}%"
                y_val = y if "%" in y or y == "指定なし" else f"{y}%"
                spec_pairs.append("指定なし" if k_val == "指定なし" and y_val == "指定なし" else f"{k_val} / {y_val}")
            
            seen = set()
            report_data['combined_spec_str'] = ", ".join([x for x in spec_pairs if not (x in seen or seen.add(x))])
        else:
            report_data['combined_spec_str'] = '―'

    elif input_mode == "✍️ 手入力":
        has_data = True
        import math
        
        if "市街化区域" in str(current_zone):
            dev_limit = 1000.0
        elif "市街化調整区域" in str(current_zone):
            dev_limit = 500.0
        elif "都市計画区域外" in str(current_zone):
            dev_limit = 10000.0
        else:
            dev_limit = 1000.0

        is_dev_required = (site_area >= dev_limit) or (current_zone == "市街化調整区域" and selected_purpose is not None and selected_purpose["cat"] in ["building", "spec_1"])
        
        max_basis, max_green = "不要", 0.0
        green_reqs = {"5%, 市みどり条例": site_area * 0.05}
        if selected_purpose is not None and selected_purpose["is_factory_law"] and (site_area >= 9000 or building_area >= 3000):
            
            # 手入力モード専用：selected_use_zoneを「準工業」最優先で判定
            use_zone_str = str(selected_use_zone)
            if "準工業" in use_zone_str:
                r_green = 0.10
                label = "10%+5%, 工場立地法"
            elif "工業専用" in use_zone_str or "工業地域" in use_zone_str:
                r_green = 0.05
                label = "5%+5%, 工場立地法"
            else:
                r_green = 0.20
                label = "20%+5%, 工場立地法"
            
            total_rate = r_green + 0.05
            green_reqs[label] = site_area * total_rate
            
        max_basis = max(green_reqs, key=green_reqs.get)
        max_green = green_reqs[max_basis]

        buffer_zone_status = "不要"
        area_ha = site_area / 10000.0
        if area_ha >= 1.0:
            buffer_zone_status = "4m以上" if area_ha < 1.5 else "5m以上" if area_ha < 5.0 else "10m以上" if area_ha < 15.0 else "15m以上" if area_ha < 25.0 else "20m以上"

        pond_volume_str = "不要"
        if site_area >= 1000.0:
            A1 = site_area / 10000.0
            alpha = 2 if A1 >= 2.0 else 1
            pond_volume_base = (122 * 0.9 - (28 / 2) * 0.6) * alpha * 30 * 60 * A1 * (1 / 360)
            pond_volume_rounded = math.ceil(pond_volume_base / 10) * 10
            pond_volume_str = f"{pond_volume_rounded:,}㎥"

        if current_zone == "―" or not current_zone:
            target_use_name, combined_spec_str = "―", "―"
        elif "市街化区域" in str(current_zone):
            target_use_name, combined_spec_str = selected_use_zone, "―"
        elif "市街化調整区域" in str(current_zone):
            target_use_name, combined_spec_str = "指定なし", "60% / 200%"
        elif "都市計画区域外" in str(current_zone):
            target_use_name, combined_spec_str = "指定なし", "指定なし"
        else:
            target_use_name, combined_spec_str = "―", "―"

        report_data = {
            "input_mode": input_mode, "geom_type": "Polygon", "center_lat": None, "center_lon": None,
            "loc_label": detailed_location, "site_area": site_area, "current_zone": current_zone,
            "target_use_name": target_use_name, "combined_spec_str": combined_spec_str,
            "is_dev_required": is_dev_required, "dosha_point_status": "手入力のため判定外", "agri_point_status": "手入力のため判定外",
            "pond_volume_str": pond_volume_str, "flood_status": "手入力のため判定外", "cultural_point_status": "手入力のため判定外",
            "forest_point_status": "手入力のため判定外", "buffer_zone_status": buffer_zone_status, "river_dist_status": "手入力のため判定外",
            "road_status": "手入力のため判定外", "gdf_dosha_none": False, "is_tomoe": False,
            "vol_min": site_area, "vol_max": site_area, "purpose_none": (selected_purpose is None),
            "max_basis": max_basis, "max_green": max_green, "dosha_red_area": 0.0, "dosha_yellow_area": 0.0
        }

    with col_btn:
        if has_data and st.button("**判定**", type="primary", use_container_width=True, key="btn_pure_bold_wide"):
            show_result_dialog(report_data)
