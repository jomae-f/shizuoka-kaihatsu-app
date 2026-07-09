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
st.title("静岡市開発行為 要件判定システム")

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
# 📄 xhtml2pdf：A4レイアウト（物理タグによる強制改行・完全対策版）
# ----------------------------------------------------
def generate_pdf(report_data):
    import io
    from xhtml2pdf import pisa
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    import os

    # ====================================================
    # 🎨 0-1. セル自動色分け用の内部関数（項目別カスタム版・確定対応）
    # ====================================================
    def get_custom_color(label_name, status_text):
        if not status_text:
            return "#ffffff"
        
        # 🟢 【一律緑】周辺の道路
        if label_name == "周辺の道路":
            return "#c8e6c9" # 薄い緑
            
        # 🔵 【一律青】周辺の河川
        if label_name == "周辺の河川":
            return "#bbdefb" # 薄い青
            
        # 🟠 【必要な時（面積）はオレンジ、不要なら無色】緑地
        if label_name == "緑地":
            if "不要" in status_text:
                return "#ffffff" # 不要のときは無色（白）
            return "#ffe0b2" # 薄いオレンジ
            
        # 🟠 【指定の幅、または「必要」のときはオレンジ、不要なら無色】緩衝帯
        if label_name == "緩衝帯":
            if any(k in status_text for k in ["必要", "m以上"]):
                return "#ffe0b2" # 薄いオレンジ
            return "#ffffff" # 不要・― などの時は無色（白）
            
        # 🔵 【必要なら青、不要なら無色】調整池
        if label_name == "調整池":
            if "不要" in status_text or status_text in ["免除", "―"]:
                return "#ffffff" # 無色
            return "#bbdefb" # 薄い青

        # 🔴🟢 【緑か赤】洪水浸水想定区域
        if label_name == "洪水浸水想定区域":
            if "区域外" in status_text or "なし" in status_text:
                return "#c8e6c9" # 薄い緑
            return "#ffcdd2" # 薄い赤

        # 🔴🟡🟢 【5段階判定を3色に集約】土砂災害警戒区域
        if label_name == "土砂災害警戒区域":
            if status_text == "レッド":
                return "#ffcdd2" # 薄い赤（完全なレッド区域のみ）
            elif any(k in status_text for k in ["イエロー", "50m以内"]):
                return "#fff9c4" # 薄い黄（混在パターンや50m以内を網羅）
            elif "区域外" in status_text:
                return "#c8e6c9" # 薄い緑
            return "#ffffff"

        # 🟡 【最優先チェック】農地法・埋蔵文化財・森林法で「50m以内」なら無条件で黄色！
        if label_name in ["農地法", "埋蔵文化財", "森林法"]:
            if "50m以内" in status_text:
                return "#fff9c4" # 薄い黄

        # 🔴🟡🟢 【各項目ごとの個別文字判定】
        if label_name == "農地法":
            if "農地あり" in status_text: return "#ffcdd2" # 薄い赤
            if "農地なし" in status_text: return "#c8e6c9" # 薄い緑
            
        if label_name == "埋蔵文化財":
            if "遺跡あり" in status_text: return "#ffcdd2" # 薄い赤
            if "遺跡なし" in status_text: return "#c8e6c9" # 薄い緑

        if label_name == "森林法":
            if "森林あり" in status_text: return "#ffcdd2" # 薄い赤
            if "森林なし" in status_text: return "#c8e6c9" # 薄い緑

        # 🔴🟡🟢 【その他の基本法令】開発許可、都市計画道路
        if any(k in status_text for k in ["必要", "制限", "レッド", "危険", "区域内"]):
            return "#ffcdd2" # 薄い赤
        elif any(k in status_text for k in ["注意", "確認", "イエロー", "要相談", "協議"]):
            return "#fff9c4" # 薄い黄
        elif any(k in status_text for k in ["不要", "許可不要", "免除", "該当なし", "区域外", "なし"]):
            return "#c8e6c9" # 薄い緑
            
        return "#ffffff"

    # 💡 コピーした .ttc ファイルのパス
    font_ttc_path = os.path.join(".", "fonts", "YuGothB.ttc") # 実際のファイル名に合わせてください

    if os.path.exists(font_ttc_path):
        try:
            # index=0 を指定して .ttc から游ゴシックの標準体を直接読み込む
            pdfmetrics.registerFont(TTFont('YuGothic', font_ttc_path, index=0))
            target_font = "YuGothic"
        except Exception as e:
            # 万が一 ReportLabのバージョンが古くてエラーになった場合のセーフティ
            target_font = "HeiseiKakuGo-W5"
    else:
        target_font = "HeiseiKakuGo-W5"

    # ====================================================
    # 🛡️ 0-2. 基本情報の取得と完全な文字列化（None漏れ徹底ガード）
    # ====================================================
    is_point_mode = report_data.get("geom_type") == "Point"
    
    site_area = report_data.get("site_area", 0.0)
    if site_area is None:
        site_area = 0.0
    area_text = f"{site_area:,.1f} ㎡" if not is_point_mode else "―"
    
    # 💡 HTML内で直接 .get() していた箇所のNoneガード
    loc_label = report_data.get('loc_label', '―')
    if loc_label is None: loc_label = '―'
    
    current_zone = report_data.get('current_zone', '―')
    if current_zone is None: current_zone = '―'
    
    target_use_name = report_data.get('target_use_name', '―')
    if target_use_name is None: target_use_name = '―'
    
    combined_spec_str = report_data.get('combined_spec_str', '―')
    if combined_spec_str is None: combined_spec_str = '―'
    
    # ====================================================
    # 📋 1. 主要法令に基づく手続要件（None完全ガード版）
    # ====================================================
    if is_point_mode:
        toshi_status = "―"
    else:
        toshi_status = "必要" if report_data.get("is_dev_required") else "不要"
    if toshi_status is None: toshi_status = "不要"
        
    # --- 農地法 ---
    agri_status = report_data.get("agri_point_status", "農地なし")
    if agri_status is None:
        agri_status = "農地なし"
    
    # --- 盛土規制法 ---
    if report_data.get("check_morido"):
        morido_status = "許可必要" if report_data.get("morido_required") else "許可不要"
    else:
        morido_status = "―"
    if morido_status is None:
        morido_status = "―"
        
    # --- 森林法 ---
    forest_status = report_data.get("forest_point_status", "森林なし")
    if forest_status is None:
        forest_status = "森林なし"
        
    # --- 都市計画道路 ---
    road_status = report_data.get("road_status", "区域外")
    if road_status is None:
        road_status = "区域外"
        
    # --- 埋蔵文化財 ---
    cultural_status = report_data.get("cultural_point_status", "遺跡なし")
    if cultural_status is None:
        cultural_status = "遺跡なし"
    
    # ====================================================
    # 🌊 2. 水害・土砂・道路・河川リスク（br改行版）
    # ====================================================
    flood_status = report_data.get("flood_status", "区域外")
    if flood_status is None:
        flood_status = "区域外"
    
    # 💡 divを廃止し、単純な <br /> に置き換えて左端のズレを防止
    river_status_raw = report_data.get("river_dist_status", "1km以内に主要河川なし")
    if river_status_raw is None:
        river_status_raw = "1km以内に主要河川なし"
    river_status = river_status_raw.replace("まで", 'まで<br />')
    
    road_display = "―"

    # 💡 ここも <br /> に置き換え
    dosha_status_raw = report_data.get("dosha_point_status", "区域外")
    if dosha_status_raw is None:
        dosha_status_raw = "区域外"
    dosha_status = dosha_status_raw.replace("イエロー、50m以内にレッド", 'イエロー、<br />50m以内にレッド')

    # ====================================================
    # 🏗️ 3. 技術基準・附帯施設要件（br改行版）
    # ====================================================
    pond_display = "不要"
    green_display = "不要"
    
    if not is_point_mode:
        # --- 調整池 ---
        pond_text = report_data.get("pond_volume_str", "―")
        is_tomoe_active = report_data.get("is_tomoe", False)
        
        if pond_text and pond_text not in ["不要", "免除", "―"]:
            if is_tomoe_active:
                pond_display = f"{pond_text}<br />（巴川流域）"
            else:
                pond_display = pond_text
        elif pond_text == "―" or pond_text is None:
            pond_display = "―"

        # --- 緑地 ---
        has_green = "静岡市" in loc_label and site_area >= 1000
        max_green = report_data.get("max_green", 0.0)
        if max_green is None: max_green = 0.0
        
        max_basis = report_data.get("max_basis", "不要")
        if max_basis is None:
            max_basis = "不要"
            
        if has_green and max_green > 0.0:
            basis_str = max_basis if max_basis.startswith("（") else f"（{max_basis}）"
            green_display = f"{max_green:,.1f} ㎡以上<br />{basis_str}"

        # --- 緩衝帯 ---
        bz_status = report_data.get("buffer_zone_status", "不要")
        if bz_status is None: bz_status = "不要"

    # ====================================================
    # 🎨 4. HTML組み立て（すべての見出し・セルへの自動色分け連動版）
    # ====================================================
    html_content = f"""
    <html>
    <head>
        <meta charset="utf-8">
            <style>
            @page {{ size: a4; margin: 0.8cm; }}
            body {{ 
                font-family: "{target_font}", sans-serif; 
                color: #121212; 
                font-size: 10.5pt; 
                line-height: 1.4; 
            }}
            .header {{ border-bottom: 2px solid #003366; padding-bottom: 8px; margin-bottom: 20px; }}
            .title {{ font-size: 18pt; font-weight: bold; color: #003366; }}
            
            table.meta-table {{ width: 100%; border-collapse: collapse; margin-bottom: 20px; }}
            table.meta-table td {{ padding: 8px 12px; border: 1px solid #555555; vertical-align: middle; }}
            /* 💡 背景色を #d1d1d1 にし、絵文字が消えた文字をド真ん中に配置します */
            table.meta-table .meta-label {{ 
                color: #121212; 
                font-weight: bold; 
                font-size: 11pt; 
                width: 25%; 
                background-color: #d1d1d1; 
                text-align: center !important; 
                vertical-align: middle; 
                padding: 8px 0px !important; 
            }}
            
            table.main-table {{ width: 100%; border-collapse: collapse; margin-top: 10px; margin-bottom: 15px; }}
            table.main-table th, table.main-table td {{ border: 1px solid #555555; font-size: 9.5pt; vertical-align: middle; }}
            
            /* 見出し(th)：上下左右すべて中央揃え */
            table.main-table th {{ 
                font-weight: bold; text-align: center;
                padding: 15px 10px; /* 最小高さを確保 */
            }}
            
            /* データ(td)：上下中央揃え ＆ 左寄せ */
            table.main-table td {{ width: 30%; text-align: left; padding: 10px 10px 10px 15px; }}
            
            .footer {{ text-align: center; font-size: 8pt; color: #121212; margin-top: 40px; padding-top: 10px; }}
            .footer-title {{ display: block; font-weight: bold; color: #121212; margin: 0 0 4px 0 !important; }}
            .cell-line {{ display: block; margin: 0 !important; padding: 1px 0 !important; line-height: 1.4; }}
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
            <tr>
                <th style="background-color: {get_custom_color('開発許可', toshi_status)};">開発許可</th>
                <td style="background-color: {get_custom_color('開発許可', toshi_status)};">{toshi_status}</td>
                
                <th style="background-color: {get_custom_color('緑地', green_display)};">緑地</th>
                <td style="background-color: {get_custom_color('緑地', green_display)};">{green_display}</td>
            </tr>
            <tr>
                <th style="background-color: {get_custom_color('土砂災害警戒区域', dosha_status)};">土砂災害警戒区域</th>
                <td style="background-color: {get_custom_color('土砂災害警戒区域', dosha_status)};">{dosha_status}</td>
                
                <th style="background-color: {get_custom_color('緩衝帯', bz_status)};">緩衝帯</th>
                <td style="background-color: {get_custom_color('緩衝帯', bz_status)};">{bz_status}</td>
            </tr>
            <tr>
                <th style="background-color: {get_custom_color('農地法', agri_status)};">農地法</th>
                <td style="background-color: {get_custom_color('農地法', agri_status)};">{agri_status}</td>
                
                <th style="background-color: {get_custom_color('調整池', pond_display)};">調整池</th>
                <td style="background-color: {get_custom_color('調整池', pond_display)};">{pond_display}</td>
            </tr>
            <tr>
                <th style="background-color: {get_custom_color('洪水浸水想定区域', flood_status)};">洪水浸水想定区域</th>
                <td style="background-color: {get_custom_color('洪水浸水想定区域', flood_status)};">{flood_status}</td>
                
                <th style="background-color: {get_custom_color('周辺の河川', river_status)};">周辺の河川</th>
                <td style="background-color: {get_custom_color('周辺の河川', river_status)};">{river_status}</td>
            </tr>
            <tr>
                <th style="background-color: {get_custom_color('埋蔵文化財', cultural_status)};">埋蔵文化財</th>
                <td style="background-color: {get_custom_color('埋蔵文化財', cultural_status)};">{cultural_status}</td>
                
                <th style="background-color: {get_custom_color('周辺の道路', road_display)};">周辺の道路</th>
                <td style="background-color: {get_custom_color('周辺の道路', road_display)};">{road_display}</td>
            </tr>
            <tr>
                <th style="background-color: {get_custom_color('森林法', forest_status)};">森林法</th>
                <td style="background-color: {get_custom_color('森林法', forest_status)};">{forest_status}</td>
                
                <th style="background-color: {get_custom_color('都市計画道路', road_status)};">都市計画道路</th>
                <td style="background-color: {get_custom_color('都市計画道路', road_status)};">{road_status}</td>
            </tr>
        </table>

        <div class="footer">
            <div class="footer-title">静岡市開発行為 要件判定システム</div>
            <div class="cell-line">本レポートはGISデータに基づく簡易判定結果であり、実際の状況や最新の指定内容とは異なる場合があります。</div>
            <div class="cell-line">実務に際しては必ず各種データの出典元情報や、各関係官庁の担当窓口にて最新の法令・要件をご確認ください。</div>
        </div>
    </body>
    </html>
    """

    # ====================================================
    # 🖨️ 5. PDF生成バイナリへの出力
    # ====================================================
    pdf_buffer = io.BytesIO()
    pisa_status = pisa.CreatePDF(io.BytesIO(html_content.encode("utf-8")), dest=pdf_buffer, encoding='utf-8')
    if pisa_status.err: 
        raise Exception("HTMLからPDFへの変換処理でエラーが発生しました。")
    return pdf_buffer.getvalue()
# ----------------------------------------------------
# 💡 ポップアップ（ダイアログ）の定義
# ----------------------------------------------------
@st.dialog("📊 開発要件 判定結果レポート", width="large")
def show_result_dialog(report_data):
    # 💡 ダイアログ上部のタイトルを無理やり大きく見せるための、隠しタイトル用スタイルCSS
    st.markdown("""
        <style>
        /* ダイアログの標準タイトルを大きく上書き */
        div[data-testid="stDialog"] h2 {
            font-size: 28px !important;
            font-weight: bold !important;
        }
        </style>
    """, unsafe_allow_html=True)

    loc_label = report_data["loc_label"]
    is_point_mode = report_data.get("geom_type") == "Point"
    area_display = f"{report_data['site_area']:,.1f} ㎡" if not is_point_mode else "―"
    
    lat = report_data.get("center_lat")
    lon = report_data.get("center_lon")
    
    # 💡 緯度経度からそれぞれのURLを組み立て
    if lat and lon:
        # 🌐 区域区分用 (op=70 の後ろに &ot=1 を追加)
        shizu_map_zone_url = f"https://city.shizuoka.geocloud.jp/webgis/?z=18&ll={lat:.6f}%2C{lon:.6f}&t=dm&mp=300&op=70&ot=1&vlf=000001"
        # 💡 文字色を他の見出しと同じ「#555」に変更し、下線だけを残しました
        zone_title_html = f'<a href="{shizu_map_zone_url}" target="_blank" style="color: #555; text-decoration: underline;">🌐 区域区分</a>'
        
        # 🏢 用途地域用 (op=70 の後ろに &ot=1 を追加)
        shizu_map_use_url = f"https://city.shizuoka.geocloud.jp/webgis/?z=18&ll={lat:.6f}%2C{lon:.6f}&t=dm&mp=300&op=70&ot=1&vlf=000002"
        # 💡 文字色を他の見出しと同じ「#555」に変更し、下線だけを残しました
        use_title_html = f'<a href="{shizu_map_use_url}" target="_blank" style="color: #555; text-decoration: underline;">🏢 用途地域</a>'
    else:
        zone_title_html = '🌐 区域区分'
        use_title_html = '🏢 用途地域'
    
    # 文字サイズと余白（padding）を大きく調整した特製ボックス
    box_html = (
        f'<div style="background-color: #f0f2f6; padding: 22px 18px; border-radius: 10px; margin-bottom: 24px; display: flex; justify-content: space-between; gap: 12px; border-left: 6px solid #a3a8b4; align-items: flex-start;">'
        f'  <div style="flex: 1.5;">'
        f'    <div style="font-size: 1.15rem; color: #555; margin-bottom: 8px; font-weight: bold;">📍 敷地所在</div>'
        f'    <div style="font-size: 1.35rem; font-weight: bold; color: #111; line-height: 1.3;">{loc_label}</div>'
        f'  </div>'
        f'  <div style="flex: 1.0; border-left: 2px solid #cbd5e1; padding-left: 14px;">'
        f'    <div style="font-size: 1.15rem; color: #555; margin-bottom: 8px; font-weight: bold;">📐 敷地面積</div>'
        f'    <div style="font-size: 1.4rem; font-weight: bold; color: #111; line-height: 1.3;">{area_display}</div>'
        f'  </div>'
        f'  <div style="flex: 1.0; border-left: 2px solid #cbd5e1; padding-left: 14px;">'
        f'    <div style="font-size: 1.15rem; color: #555; margin-bottom: 8px; font-weight: bold;">{zone_title_html}</div>'
        f'    <div style="font-size: 1.35rem; font-weight: bold; color: #111; line-height: 1.3;">{report_data["current_zone"]}</div>'
        f'  </div>'
        f'  <div style="flex: 1.5; border-left: 2px solid #cbd5e1; padding-left: 14px;">'
        f'    <div style="font-size: 1.15rem; color: #555; margin-bottom: 8px; font-weight: bold;">{use_title_html}</div>'
        f'    <div style="font-size: 1.35rem; font-weight: bold; color: #111; line-height: 1.3;">{report_data["target_use_name"]}</div>'
        f'  </div>'
        f'  <div style="flex: 1.0; border-left: 2px solid #cbd5e1; padding-left: 14px;">'
        f'  <div style="flex: 1.0; border-left: none; padding-left: 0px;">'
        f'    <div style="font-size: 1.15rem; color: #555; margin-bottom: 8px; font-weight: bold;">📐 建蔽率 / 容積率</div>'
        f'    <div style="font-size: 1.4rem; font-weight: bold; color: #111; line-height: 1.3;">{report_data.get("combined_spec_str", "―")}</div>'
        f'  </div>'
        f'</div>'
    )
    st.markdown(box_html, unsafe_allow_html=True)
    
    try:
        pdf_data = generate_pdf(report_data)
        st.download_button(
            label="📄 判定結果レポートをPDFでダウンロード ",
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
        # --- 🏗️ 開発許可判定（緑地同期版） ---
        if not is_point_mode:
            is_dev_required = report_data.get("is_dev_required", False)

            if is_dev_required:
                bg_color, border_color, text_color, status_text, icon = "#ffebee", "#ef5350", "#c62828", "必要", "🚨"
            else:
                bg_color, border_color, text_color, status_text, icon = "#e8f5e9", "#66bb6a", "#2e7d32", "不要", "✅"

            # 💡 緑地と完全に同じ padding: 16px、下段の display: flex (横並び) 構造に統一
            st.markdown(
                f'<div style="background-color: {bg_color}; border-left: 5px solid {border_color}; padding: 16px; border-radius: 4px; margin-bottom: 16px;">'
                f'  <div style="font-size: 1.3rem; font-weight: bold; color: {text_color}; margin-bottom: 8px;">{icon} 【開発許可】</div>'
                f'  <div style="display: flex; justify-content: flex-end; align-items: center; color: {text_color}; font-weight: bold; gap: 8px; line-height: 1.4;">'
                f'    <span style="font-size: 1.4rem;">{status_text}</span>'
                f'  </div>'
                f'</div>',
                unsafe_allow_html=True
            )

        # --- 🚨 土砂災害警戒区域UI表示（5段階文字列一致版・【】あり） ---
        if report_data.get("gdf_dosha_none"):
            st.caption("ℹ️ 土砂災害警戒区域データが見つかりません。")
        else:
            status = report_data.get("dosha_point_status", "区域外")

            # 各ステータスに応じた適切なカラーパレットの割り当て
            if status == "レッド":
                bg_color, border_color, text_color = "#ffebee", "#ef5350", "#c62828"
            elif status in ["イエロー、50m以内にレッド", "イエロー", "50m以内にレッド", "50m以内にイエロー"]:
                bg_color, border_color, text_color = "#fff3e0", "#ffb74d", "#e65100"
            else:
                bg_color, border_color, text_color = "#e8f5e9", "#66bb6a", "#2e7d32"

            lat = report_data.get("center_lat")
            lon = report_data.get("center_lon")
            
            if lat and lon:
                shizu_map_dosha_url = f"https://city.shizuoka.geocloud.jp/webgis/?z=18&ll={lat:.6f}%2C{lon:.6f}&t=roadmap&mp=100&op=70&vlf=007f80"
                # 💡 見出しに【】を戻しました
                dosha_title_html = f'<a href="{shizu_map_dosha_url}" target="_blank" style="color: {text_color}; text-decoration: underline;">【土砂災害警戒区域】</a>'
            else:
                dosha_title_html = '【土砂災害警戒区域】'

            st.markdown(
                f'<div style="background-color: {bg_color}; border-left: 5px solid {border_color}; padding: 16px; border-radius: 4px; margin-bottom: 16px;">'
                f'  <div style="font-size: 1.3rem; font-weight: bold; color: {text_color}; margin-bottom: 8px;">🚨 {dosha_title_html}</div>'
                f'  <div style="display: flex; justify-content: flex-end; align-items: center; color: {text_color}; font-weight: bold; gap: 8px; line-height: 1.4;">'
                f'    <span style="font-size: 1.4rem;">{status}</span>'
                f'  </div>'
                f'</div>',
                unsafe_allow_html=True
            )
            
        # --- 🚜 農地法UI表示 ---
        agri_status = report_data.get("agri_point_status", "農地なし")

        if agri_status == "農地あり":
            # 🚨 赤系（他項目のレッドと同じ）
            bg_color, border_color, text_color = "#ffebee", "#ef5350", "#c62828"
            status_text = "農地あり"
        elif agri_status == "50m以内に農地":
            # 🟡 黄系（他項目のイエロー・近傍と同じ）
            bg_color, border_color, text_color = "#fff3e0", "#ffb74d", "#e65100"
            status_text = "50m以内に農地"
        else:
            # ✅ 緑系（他項目の区域外・不要と同じ）
            bg_color, border_color, text_color = "#e8f5e9", "#66bb6a", "#2e7d32"
            status_text = "農地なし"

        # 💡 一旦、確実に開くトップページのURLを設定
        emaff_url = "https://map.maff.go.jp/"
        agri_title_html = f'<a href="{emaff_url}" target="_blank" style="color: {text_color}; text-decoration: underline;">【農地法】</a>'

        # 💡 下段を column（縦並び）から緑地と同じ flex (横並び) 構造に統一
        st.markdown(
            f'<div style="background-color: {bg_color}; border-left: 5px solid {border_color}; padding: 16px; border-radius: 4px; margin-bottom: 16px;">'
            f'  <div style="font-size: 1.3rem; font-weight: bold; color: {text_color}; margin-bottom: 8px;">🚜 {agri_title_html}</div>'
            f'  <div style="display: flex; justify-content: flex-end; align-items: center; color: {text_color}; font-weight: bold; gap: 8px; line-height: 1.4;">'
            f'    <span style="font-size: 1.4rem;">{status_text}</span>'
            f'  </div>'
            f'</div>',
            unsafe_allow_html=True
        )
            
        # --- 🚧 盛土規制法UI表示（緑地同期版・色修正） ---
        if report_data["check_morido"]:
            if report_data["morido_required"]:
                # 🚨 赤系（他項目のレッド・必要・ありと同じ）
                bg_color, border_color, text_color = "#ffebee", "#ef5350", "#c62828"
                status_text = "許可必要"
                icon = "🚨"
            else:
                # ✅ 緑系（対象外・不要）
                bg_color, border_color, text_color = "#e8f5e9", "#66bb6a", "#2e7d32"
                status_text = "許可不要"
                icon = "✅"

            # 💡 構造・サイズは「緑地同期版」を完全維持
            st.markdown(
                f'<div style="background-color: {bg_color}; border-left: 5px solid {border_color}; padding: 16px; border-radius: 4px; margin-bottom: 16px;">'
                f'  <div style="font-size: 1.3rem; font-weight: bold; color: {text_color}; margin-bottom: 8px;">{icon} 【盛土規制法】</div>'
                f'  <div style="display: flex; justify-content: flex-end; align-items: center; color: {text_color}; font-weight: bold; gap: 8px; line-height: 1.4;">'
                f'    <span style="font-size: 1.4rem;">{status_text}</span>'
                f'  </div>'
                f'</div>',
                unsafe_allow_html=True
            )

        # --- 🌊 洪水浸水想定区域UI表示（リンク付き改訂版・緑地同期） ---
        status = report_data.get("flood_status", "区域外")
        
        if status == "区域外":
            bg_color, border_color, text_color = "#e8f5e9", "#66bb6a", "#2e7d32"
        else:
            bg_color, border_color, text_color = "#ffebee", "#ef5350", "#c62828"

        # 💡 緯度経度を取得して「しずマップ（洪水用）」のURLを組み立て
        lat = report_data.get("center_lat")
        lon = report_data.get("center_lon")
        
        if lat and lon:
            # ④t=roadmap&mp=101 ⑤op=70 [ot=1] ⑥vlf=0003ffffffff... を適用
            shizu_map_flood_url = f"https://city.shizuoka.geocloud.jp/webgis/?z=18&ll={lat:.6f}%2C{lon:.6f}&t=roadmap&mp=101&op=70&ot=1&vlf=0003ffffffffffffffffffffffffff"
            # 見出しをリンク化（ステータスの文字色と同化させます）
            flood_title_html = f'<a href="{shizu_map_flood_url}" target="_blank" style="color: {text_color}; text-decoration: underline;">【洪水浸水想定区域】</a>'
        else:
            flood_title_html = '【洪水浸水想定区域】'

        # 💡 見出しの無駄な<div>の改行とline-heightを排除し、下段を緑地と同じflex横並びに統一
        st.markdown(
            f'<div style="background-color: {bg_color}; border-left: 5px solid {border_color}; padding: 16px; border-radius: 4px; margin-bottom: 16px;">'
            f'  <div style="font-size: 1.3rem; font-weight: bold; color: {text_color}; margin-bottom: 8px;">🌊 {flood_title_html}</div>'
            f'  <div style="display: flex; justify-content: flex-end; align-items: center; color: {text_color}; font-weight: bold; gap: 8px; line-height: 1.4;">'
            f'    <span style="font-size: 1.4rem;">{status}</span>'
            f'  </div>'
            f'</div>',
            unsafe_allow_html=True
        )

        # --- 🏺 埋蔵文化財UI表示（リンク付き改訂版・緑地同期） ---
        status = report_data.get("cultural_point_status", "✅ 対象外")
        
        if "対象外" in status:
            bg_color, border_color, text_color = "#e8f5e9", "#66bb6a", "#2e7d32"
        else:
            bg_color, border_color, text_color = "#fff3e0", "#ffb74d", "#e65100"

        # 💡 緯度経度を取得して「しずマップ（文化財用）」のURLを組み立て
        lat = report_data.get("center_lat")
        lon = report_data.get("center_lon")
        
        if lat and lon:
            # ④t=roadmap&mp=402 ⑤op=70 [ot=1] ⑥vlf=-1 を適用
            shizu_map_cultural_url = f"https://city.shizuoka.geocloud.jp/webgis/?z=18&ll={lat:.6f}%2C{lon:.6f}&t=roadmap&mp=402&op=70&ot=1&vlf=-1"
            # 見出しをリンク化（ステータスの文字色と同化させます）
            cultural_title_html = f'<a href="{shizu_map_cultural_url}" target="_blank" style="color: {text_color}; text-decoration: underline;">【埋蔵文化財】</a>'
        else:
            cultural_title_html = '【埋蔵文化財】'

        # 💡 見出しの余分な<div>ネストとline-heightを排除し、右側ステータスをflex横並び（align-items: center）に統一
        st.markdown(
            f'<div style="background-color: {bg_color}; border-left: 5px solid {border_color}; padding: 16px; border-radius: 4px; margin-bottom: 16px;">'
            f'  <div style="font-size: 1.3rem; font-weight: bold; color: {text_color}; margin-bottom: 8px;">🏺 {cultural_title_html}</div>'
            f'  <div style="display: flex; justify-content: flex-end; align-items: center; color: {text_color}; font-weight: bold; gap: 8px; line-height: 1.4;">'
            f'    <span style="font-size: 1.4rem;">{status}</span>'
            f'  </div>'
            f'</div>',
            unsafe_allow_html=True
        )
     
        # --- 🌲 森林法UI表示 ---
        forest_status = report_data.get("forest_point_status", "森林なし")

        if forest_status == "森林あり":
            # 🚨 赤系（直撃）
            bg_color, border_color, text_color = "#ffebee", "#ef5350", "#c62828"
            status_text = "森林あり"
        elif forest_status == "50m以内に森林":
            # 🟡 黄系（50m近傍）
            bg_color, border_color, text_color = "#fff3e0", "#ffb74d", "#e65100"
            status_text = "50m以内に森林"
        else:
            # ✅ 緑系（非該当）
            bg_color, border_color, text_color = "#e8f5e9", "#66bb6a", "#2e7d32"
            status_text = "森林なし"

        # 💡 同意画面に座標を引き連れていくURLにします
        lat = report_data.get("center_lat")
        lon = report_data.get("center_lon")
        
        if lat and lon:
            # 同意画面でURL欄に残るため、開いた後に末尾の「#15/緯度/経度」の部分をコピペして使えます
            fcloud_url = f"https://fcloud.pref.shizuoka.jp/fgis/?version=1.26.0525.a#15/{lat:.5f}/{lon:.5f}"
            forest_title_html = f'<a href="{fcloud_url}" target="_blank" style="color: {text_color}; text-decoration: underline;">【森林法】</a>'
        else:
            forest_title_html = '【森林法】'

        # 💡 下段を column（縦並び）から緑地と同じ flex (横並び) 構造に統一
        st.markdown(
            f'<div style="background-color: {bg_color}; border-left: 5px solid {border_color}; padding: 16px; border-radius: 4px; margin-bottom: 16px;">'
            f'  <div style="font-size: 1.3rem; font-weight: bold; color: {text_color}; margin-bottom: 8px;">🌲 {forest_title_html}</div>'
            f'  <div style="display: flex; justify-content: flex-end; align-items: center; color: {text_color}; font-weight: bold; gap: 8px; line-height: 1.4;">'
            f'    <span style="font-size: 1.4rem;">{status_text}</span>'
            f'  </div>'
            f'</div>',
            unsafe_allow_html=True
        )

    with diag_col2:
        # --- 🌲 緑地UI表示 ---  
        if "静岡市" in loc_label and report_data["site_area"] >= 1000:
            # 1. 工場立地法かどうかの判定（元の根拠テキストに「工場」が含まれるかで判定）
            is_factory = "工場" in str(report_data.get("max_basis", ""))
            
            import math
            if is_factory:
                # 工場立地法の場合は25%（20%+5%）で面積を計算し、小数第2位で切り上げ
                raw_green_area = report_data["site_area"] * 0.25
                green_area_val = math.ceil(raw_green_area * 100) / 100
                basis_text = "20%+5%, 工場立地法"
            else:
                # 通常（5%）の場合も、既存の max_green をベースに小数第2位で切り上げ
                raw_green_area = report_data["max_green"]
                green_area_val = math.ceil(raw_green_area * 100) / 100
                basis_text = "5%, 市みどり条例"
            
            # 💡 line-height: 1.4 を追加し、align-items: center に変更して高さを固定
            st.markdown(
                f'<div style="background-color: #fff3e0; border-left: 5px solid #ffb74d; padding: 16px; border-radius: 4px; margin-bottom: 16px;">'
                f'  <div style="font-size: 1.3rem; font-weight: bold; color: #e65100; margin-bottom: 8px;">🌲 【緑地】</div>'
                f'  <div style="display: flex; justify-content: flex-end; align-items: center; color: #e65100; font-weight: bold; gap: 8px; line-height: 1.4;">'
                f'    <span style="font-size: 1.15rem;">（{basis_text}）</span>'
                f'    <span style="font-size: 1.4rem;">{green_area_val:,.2f}㎡以上</span>'
                f'  </div>'
                f'</div>',
                unsafe_allow_html=True
            )
        else:
            st.markdown(
                f'<div style="background-color: #fff3e0; border-left: 5px solid #ffb74d; padding: 16px; border-radius: 4px; margin-bottom: 16px;">'
                f'  <div style="font-size: 1.3rem; font-weight: bold; color: #e65100; margin-bottom: 8px;">🌲 【緑地】</div>'
                f'  <div style="display: flex; flex-direction: column; align-items: flex-end; color: #e65100; line-height: 1.4;">'
                f'    <span style="font-size: 1.4rem; font-weight: bold;">不要</span>'
                f'  </div>'
                f'</div>',
                unsafe_allow_html=True
            )

        # --- 🌳 緩衝帯UI表示（緑地同期版） ---
        bz_status = report_data.get("buffer_zone_status", "不要")

        if bz_status != "不要":
            bg_color, border_color, text_color = "#fff3e0", "#ffb74d", "#e65100"

            # 💡 構造を緑地と完全に同一に統一
            st.markdown(
                f'<div style="background-color: {bg_color}; border-left: 5px solid {border_color}; padding: 16px; border-radius: 4px; margin-bottom: 16px;">'
                f'  <div style="font-size: 1.3rem; font-weight: bold; color: {text_color}; margin-bottom: 8px;">🌳 【緩衝帯】</div>'
                f'  <div style="display: flex; justify-content: flex-end; align-items: center; color: {text_color}; font-weight: bold; gap: 8px; line-height: 1.4;">'
                f'    <span style="font-size: 1.4rem;">{bz_status}</span>'
                f'  </div>'
                f'</div>',
                unsafe_allow_html=True
            )

        # --- 💧 調整池UI表示 ---
        if not is_point_mode:
            is_tomoe_active = report_data.get("is_tomoe", False)
            left_text = f'<span style="font-size: 1.15rem;">（巴川流域）</span>' if is_tomoe_active else ""
            
            # 💡 構造・余白・フォント配置を完全に緑地と完全同期
            st.markdown(
                f'<div style="background-color: #e8f4f8; border-left: 5px solid #29b6f6; padding: 16px; border-radius: 4px; margin-bottom: 16px;">'
                f'  <div style="font-size: 1.3rem; font-weight: bold; color: #0288d1; margin-bottom: 8px;">💧 【調整池】</div>'
                f'  <div style="display: flex; justify-content: flex-end; align-items: center; color: #0288d1; font-weight: bold; gap: 8px; line-height: 1.4;">'
                f'    {left_text}'
                f'    <span style="font-size: 1.4rem;">{report_data.get("pond_volume_str", "―")}</span>'
                f'  </div>'
                f'</div>',
                unsafe_allow_html=True
            )

        # --- 🏞️ 河川距離UI表示 ---
        r_dist_status = report_data.get("river_dist_status", "1km以内に主要河川なし")

        if "1km以内に主要河川なし" not in r_dist_status:
            bg_color, border_color, text_color = "#e8f4f8", "#29b6f6", "#0288d1"
            status_text = r_dist_status
        else:
            bg_color, border_color, text_color = "#e8f5e9", "#66bb6a", "#2e7d32"
            status_text = "1km以内に主要河川なし"

        # 💡 関係ない部分は変えずに、ここにしずマップのURL生成だけを追加
        lat = report_data.get("center_lat")
        lon = report_data.get("center_lon")
        
        if lat and lon:
            # ④t=roadmap&mp=308 ⑤op=70 &ot=1 ⑥vlf=-1 を適用
            shizu_map_river_url = f"https://city.shizuoka.geocloud.jp/webgis/?z=18&ll={lat:.6f}%2C{lon:.6f}&t=roadmap&mp=308&op=70&ot=1&vlf=-1"
            river_title_html = f'<a href="{shizu_map_river_url}" target="_blank" style="color: {text_color}; text-decoration: underline;">【周辺の河川】</a>'
        else:
            river_title_html = '【周辺の河川】'

        # 💡 下段を緑地と同じ flex 横並び（align-items: center）構造に統一
        st.markdown(
            f'<div style="background-color: {bg_color}; border-left: 5px solid {border_color}; padding: 16px; border-radius: 4px; margin-bottom: 16px;">'
            f'  <div style="font-size: 1.3rem; font-weight: bold; color: {text_color}; margin-bottom: 8px;">🏞️ {river_title_html}</div>'
            f'  <div style="display: flex; justify-content: flex-end; align-items: center; color: {text_color}; font-weight: bold; gap: 8px; line-height: 1.4;">'
            f'    <span style="font-size: 1.4rem;">{status_text}</span>'
            f'  </div>'
            f'</div>',
            unsafe_allow_html=True
        )

        # --- 🚗 周辺の道路（新規項目・南東引き戻し補正版） ---
        lat = report_data.get("center_lat")
        lon = report_data.get("center_lon")

        # 全体の調和を考えた緑系のカラーパレット
        bg_green = "#e8f5e9"
        border_green = "#66bb6a"
        text_green = "#2e7d32"

        if lat and lon:
            # 💡 北西へのズレを相殺するため、符号を反転（南東へ引き戻す）
            corrected_lat = lat - 0.00328
            corrected_lon = lon + 0.00321

            # 補正済みの座標をURLに適用
            road_url = f"https://www2.wagmap.jp/shizuoka/Map?mid=1&mpx={corrected_lon:.6f}&mpy={corrected_lat:.6f}&bsw=1200&bsh=800"
            road_title_html = f'<a href="{road_url}" target="_blank" style="color: {text_green}; text-decoration: underline;">【周辺の道路】</a>'
            road_text_html = f'<a href="{road_url}" target="_blank" style="color: {text_green}; text-decoration: underline; font-size: 1.4rem; font-weight: bold;">🔗静岡市地図情報サービス</a>'
        else:
            road_title_html = '【周辺の道路】'
            road_text_html = f'<span style="font-size: 1.4rem; font-weight: bold; color: {text_green};">🔗静岡市地図情報サービス</span>'

        # 💡 下段の構造を column（縦並び）から緑地と同じ flex 横並び（align-items: center）構造に統一
        st.markdown(
            f'<div style="background-color: {bg_green}; border-left: 5px solid {border_green}; padding: 16px; border-radius: 4px; margin-bottom: 16px;">'
            f'  <div style="font-size: 1.3rem; font-weight: bold; color: {text_green}; margin-bottom: 8px;">🚗 {road_title_html}</div>'
            f'  <div style="display: flex; justify-content: flex-end; align-items: center; font-weight: bold; gap: 8px; line-height: 1.4;">'
            f'    {road_text_html}'
            f'  </div>'
            f'</div>',
            unsafe_allow_html=True
        )

        # --- 🛣️ 都市計画道路UI表示（リンク付き改訂版・緑地同期） ---
        status = report_data.get("road_status", "区域外")
        
        if status == "区域外":
            bg_color, border_color, text_color = "#e8f5e9", "#66bb6a", "#2e7d32"
        else:
            bg_color, border_color, text_color = "#ffebee", "#ef5350", "#c62828"

        # 💡 緯度経度を取得して「しずマップ（都市計画道路用）」のURLを組み立て
        lat = report_data.get("center_lat")
        lon = report_data.get("center_lon")
        
        if lat and lon:
            # ④t=dm&mp=300 ⑤op=70 ⑥vlf=000010000000 (ot=1も追加)
            shizu_map_road_url = f"https://city.shizuoka.geocloud.jp/webgis/?z=18&ll={lat:.6f}%2C{lon:.6f}&t=dm&mp=300&op=70&ot=1&vlf=000010000000"
            # 見出しをリンク化（ステータスの文字色と同化させます）
            road_title_html = f'<a href="{shizu_map_road_url}" target="_blank" style="color: {text_color}; text-decoration: underline;">【都市計画道路】</a>'
        else:
            road_title_html = '【都市計画道路】'

        # 💡 見出しの無駄な余白を排除し、下段を緑地と同じ flex 横並び（align-items: center）構造に統一
        st.markdown(
            f'<div style="background-color: {bg_color}; border-left: 5px solid {border_color}; padding: 16px; border-radius: 4px; margin-bottom: 16px;">'
            f'  <div style="font-size: 1.3rem; font-weight: bold; color: {text_color}; margin-bottom: 8px;">🛣️ {road_title_html}</div>'
            f'  <div style="display: flex; justify-content: flex-end; align-items: center; color: {text_color}; font-weight: bold; gap: 8px; line-height: 1.4;">'
            f'    <span style="font-size: 1.4rem;">{status}</span>'
            f'  </div>'
            f'</div>',
            unsafe_allow_html=True
        )

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
        
    m = folium.Map(location=[34.9792, 138.3831], zoom_start=15, max_zoom=20, control_scale=True)
    folium.TileLayer(tiles='https://mt1.google.com/vt/lyrs=m&x={x}&y={y}&z={z}', attr='Google', name='Google Map', max_zoom=20).add_to(m)
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
            # 💡 通常（ポリゴン）モードでも、外縁から50mの検索用バッファポリゴンを生成する
            user_gdf_m = user_gdf.to_crs(epsg=6676)
            buffer_geom_m = user_gdf_m.geometry.iloc[0].buffer(50.0)
            buffer_gdf = gpd.GeoDataFrame(geometry=[buffer_geom_m], crs="EPSG:6676")
            buffer_gdf_4326 = buffer_gdf.to_crs(epsg=4326)
            search_poly = buffer_gdf_4326.geometry.iloc[0]
            center_lon = user_geom.centroid.x
            center_lat = user_geom.centroid.y

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
                
                # 99%以上の場合は単一区域として判定
                if shigaika_area >= site_area * 0.99:
                    current_zone = "市街化区域"
                elif chousei_area >= site_area * 0.99:
                    current_zone = "市街化調整区域"
                else:
                    # 99%未満＝またがっている（混在）場合の処理
                    zones = []
                    if shigaika_area > 1.0:  # 1㎡以上の重複があれば追加
                        zones.append("市街化区域")
                    if chousei_area > 1.0:
                        zones.append("市街化調整区域")
                        
                    # 念のため、どちらでもない残りの面積（都市計画区域外）が1㎡以上ある場合
                    rem_area = site_area - (shigaika_area + chousei_area)
                    if rem_area > 1.0:
                        zones.append("都市計画区域外")
                        
                    # 該当する区域を画面・PDF上で綺麗に改行（<br>）して結合
                    if zones:
                        current_zone = "<br>".join(zones)
                    else:
                        current_zone = "都市計画区域外"

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
                            # 99%未満（またがっている）場合の処理を変更
                            distinct_uses = inter_use["A29_005"].unique()
                            # 「（混在）」を削除し、各用途地域を改行（<br>）で繋ぎます
                            use_choice = "<br>".join(distinct_uses)
                            
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

        # --- 🚜 農地法判定 ---
        agri_point_status = "農地なし"  # 💡 初期値
        if gdf_agri is not None:
            # 50mバッファ(search_poly)の範囲内に農地データがあるかインターセクション
            possible_agri = gdf_agri.iloc[list(gdf_agri.sindex.intersection(search_poly.bounds))]
            
            if not possible_agri.empty:
                # 50mバッファ（近傍判定用のポリゴン）に交差するか
                hit_agri_near = possible_agri[possible_agri.intersects(search_poly)]
                
                if not hit_agri_near.empty:
                    agri_point_status = "50m以内に農地"  # 一旦「50m以内」とする
                    
                    # クリック地点（Point）または ユーザーの描画ポリゴン（Polygon）そのものを取得
                    target_geom = user_gdf.geometry.iloc[0]
                    
                    # 敷地（または指定地点）そのものに直撃しているレコードを抽出
                    direct_hits = hit_agri_near[hit_agri_near.intersects(target_geom)]
                    
                    # 範囲指定（通常モード）で、重複面積が1.0㎡以下の微小な誤差は除外したい場合は
                    # ここで面積判定を挟むことも可能ですが、今回は「一部分でも該当する場合」のためシンプルに判定します
                    if not direct_hits.empty:
                        agri_point_status = "農地あり"

        # --- 🌲 森林法判定 ---
        forest_point_status = "森林なし"  # 💡 初期値
        if gdf_forest is not None:
            # 50mバッファ(search_poly)の範囲内に森林データがあるかインターセクション
            possible_forest = gdf_forest.iloc[list(gdf_forest.sindex.intersection(search_poly.bounds))]
            
            if not possible_forest.empty:
                # 50mバッファ（近傍判定用のポリゴン）に交差するか
                hit_forest_near = possible_forest[possible_forest.intersects(search_poly)]
                
                if not hit_forest_near.empty:
                    forest_point_status = "50m以内に森林"  # 一旦「50m以内」とする
                    
                    # 敷地（または指定地点）そのもののジオメトリ
                    target_geom = user_gdf.geometry.iloc[0]
                    
                    # 敷地そのものに直撃しているレコードを抽出
                    direct_hits = hit_forest_near[hit_forest_near.intersects(target_geom)]
                    
                    if not direct_hits.empty:
                        forest_point_status = "森林あり"

        # --- 🏞️ 河川距離判定 ---
        river_dist_status = "1km以内に主要河川なし"  # 💡 初期値
        
        # 互換性のために残す（古いPDF機能等で参照されている場合用）
        has_river_dist = False
        nearest_river_name = "名称不明の河川"
        nearest_river_class = "準用・普通河川等"
        nearest_river_dist = 0

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
                    
                    # 💡 【新規】10m単位に四捨五入した距離を作成
                    rounded_dist = int(round(nearest_river_dist, -1))
                    
                    # 💡 【新規】1行で右寄せ表示するためのステータス文言をここで組み立てる
                    river_dist_status = f"{nearest_river_class} {nearest_river_name}まで 約 {rounded_dist:,}m"

        # --- 🌊 洪水浸水想定区域判定 ---
        flood_hit = False
        flood_river_name = ""
        flood_rank_code = ""
        flood_desc = ""

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
                    
                    # 元のマスタ辞書
                    rank_desc = {"1":"0.5m未満", "2":"0.5m〜3.0m未満", "3":"3.0m〜5.0m未満", "4":"5.0m〜10.0m未満", "5":"10.0m〜20.0m未満", "6":"20.0m以上"}
                    flood_desc = rank_desc.get(flood_rank_code, "（要窓口確認）")

        # 💡 【他項目と揃える統合処理】
        # 不要な「未満」を消去し、他と100%整合性を保つ flood_status 変数を作る
        if flood_hit and flood_desc:
            flood_status = str(flood_desc).replace("未満", "").strip()
        else:
            flood_status = "区域外"

        # --- 土砂災害警戒区域判定（新5段階判定版） ---
        dosha_point_status = "区域外"  # 初期値
        dosha_near = False          # 互換性維持
        dosha_hit = False           # 互換性維持
        dosha_yellow_area = 0.0     
        dosha_red_area = 0.0        

        if gdf_dosha is not None:
            # 💡 共通の50m拡大枠(search_poly)に交差するデータを抽出
            possible_dosha = gdf_dosha.iloc[list(gdf_dosha.sindex.intersection(search_poly.bounds))]
            
            if not possible_dosha.empty:
                # 50mバッファの中に土砂災害区域が存在するか
                hit_dosha_near = possible_dosha[possible_dosha.intersects(search_poly)].copy()
                
                if not hit_dosha_near.empty:
                    # 文字列のトリムと正規化をここで一括で行っておく
                    hit_dosha_near['A33_002_str'] = hit_dosha_near['A33_002'].astype(str).str.strip()
                    
                    dosha_near = True  # 何かしら50m以内にある
                    target_geom = user_gdf.geometry.iloc[0]
                    
                    # 生の敷地そのものに直接ヒットしているレコードを抽出
                    direct_hits = hit_dosha_near[hit_dosha_near.intersects(target_geom)].copy()
                    
                    if not direct_hits.empty:
                        dosha_hit = True
                        
                        # ① レッドに該当する場合
                        if (direct_hits['A33_002_str'] == '2').any():
                            dosha_point_status = "レッド"
                        
                        # イエロー直撃時の判定
                        elif (direct_hits['A33_002_str'] == '1').any():
                            # ② イエローに該当し、かつ50m以内にレッドが存在する場合
                            if (hit_dosha_near['A33_002_str'] == '2').any():
                                dosha_point_status = "イエロー、50m以内にレッド"
                            # ③ イエローにのみ該当する場合（50m以内にもレッドが一切ない）
                            else:
                                dosha_point_status = "イエロー"
                        
                        # 🗺️ ポリゴンモードの時だけ、PDFの面積計算用に値を残す処理
                        if geom_type != "Point":
                            inter_dosha = gpd.overlay(user_gdf, direct_hits, how='intersection')
                            if not inter_dosha.empty:
                                inter_dosha['calc_area'] = inter_dosha.geometry.map(calculate_area_m2)
                                inter_dosha['A33_002_str'] = inter_dosha['A33_002'].astype(str).str.strip()
                                dosha_yellow_area = inter_dosha[inter_dosha['A33_002_str'] == '1']['calc_area'].sum()
                                dosha_red_area = inter_dosha[inter_dosha['A33_002_str'] == '2']['calc_area'].sum()
                    
                    else:
                        # 敷地直撃はないが、50m以内（近傍）のケース
                        # ④ いずれにも該当しないかつイエローから50m以内の場合（※レッドが50m以内にあれば優先）
                        if (hit_dosha_near['A33_002_str'] == '2').any():
                            dosha_point_status = "50m以内にレッド"  # 厳密な条件網羅のため追加
                        elif (hit_dosha_near['A33_002_str'] == '1').any():
                            dosha_point_status = "50m以内にイエロー"
                        else:
                            # ⑤ いずれにも該当せず50mより遠い場合
                            dosha_point_status = "区域外"

        # --- 🛣️ 都市計画道路判定 ---
        road_point_status = "計画なし"  # 💡 初期値
        
        if gdf_road is not None:
            # 💡 都市計画道路専用に「10mバッファ」をその場で生成する
            user_gdf_m = user_gdf.to_crs(epsg=6676)
            road_buffer_geom_m = user_gdf_m.geometry.iloc[0].buffer(10.0) # 10mバッファ
            road_buffer_gdf = gpd.GeoDataFrame(geometry=[road_buffer_geom_m], crs="EPSG:6676")
            road_buffer_gdf_4326 = road_buffer_gdf.to_crs(epsg=4326)
            road_search_poly = road_buffer_gdf_4326.geometry.iloc[0]

            # 10mバッファの範囲内にあるデータを抽出
            possible_road = gdf_road.iloc[list(gdf_road.sindex.intersection(road_search_poly.bounds))]
            
            if not possible_road.empty:
                # 10m以内に都市計画道路が存在するか
                hit_road_near = possible_road[possible_road.intersects(road_search_poly)]
                
                if not hit_road_near.empty:
                    road_point_status = "10m以内に計画あり"  # 一旦「10m以内」とする
                    
                    # 生の敷地（点またはポリゴン）そのものを取得
                    target_geom = user_gdf.geometry.iloc[0]
                    
                    # 敷地そのものに直撃しているレコードを抽出
                    direct_hits = hit_road_near[hit_road_near.intersects(target_geom)]
                    
                    if not direct_hits.empty:
                        road_point_status = "計画あり"

        # --- 🏺 埋蔵文化財判定 ---
        cultural_point_status = "遺跡なし"  # 💡 初期値
        if gdf_cultural is not None:
            # 50mバッファ(search_poly)の範囲内に埋蔵文化財データがあるかインターセクション
            possible_cultural = gdf_cultural.iloc[list(gdf_cultural.sindex.intersection(search_poly.bounds))]
            
            if not possible_cultural.empty:
                # 50mバッファ（近傍判定用のポリゴン）に交差するか
                hit_cultural_near = possible_cultural[possible_cultural.intersects(search_poly)]
                
                if not hit_cultural_near.empty:
                    cultural_point_status = "50m以内に遺跡"  # 一旦「50m以内」とする
                    
                    # 敷地（または指定地点）そのもののジオメトリ
                    target_geom = user_gdf.geometry.iloc[0]
                    
                    # 敷地そのものに直撃しているレコードを抽出
                    direct_hits = hit_cultural_near[hit_cultural_near.intersects(target_geom)]
                    
                    if not direct_hits.empty:
                        cultural_point_status = "遺跡あり"

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
                        
                        # 99%以上の場合は単一の用途地域として判定
                        if area_summary.max() >= site_area * 0.99:
                            use_choice = area_summary.idxmax()
                        else:
                            # 99%未満（またがっている）場合の処理を変更：「（混在）」を削除し改行（<br>）で繋ぐ
                            use_choice = "<br>".join(area_summary.index)
                    else: use_choice = "指定なし"

    use_district = "others"
    if "準工業" in use_choice: use_district = "quasi_industrial"
    elif "工業" in use_choice or "工業専用" in use_choice: use_district = "industrial"

    if has_data:
        dev_limit = 1000.0 if current_zone == "市街化区域" else 500.0
        is_dev_required = (site_area >= dev_limit) or (current_zone == "市街化調整区域" and selected_purpose is not None and selected_purpose["cat"] in ["building", "spec_1"])
        morido_required = morido_area > 500 or kiri_height > 2.0 or mori_height > 1.0 or ((kiri_height + mori_height) > 2.0 and kiri_height > 0 and mori_height > 0)
        
        # --- 🌳 緑地 判定ロジック（緑地専用・UI文言完全同期版） ---
        max_basis, max_green = "不要", 0.0
        
        if geom_type != "Point":
            # 💡 辞書のキーをご指定のUI表記「5%, 市みどり条例」に修正
            green_reqs = {"5%, 市みどり条例": site_area * 0.05}
            
            # 工場立地法の判定
            if selected_purpose is not None and selected_purpose["is_factory_law"] and (site_area >= 9000 or building_area >= 3000):
                r_green = 0.05 if use_district == "industrial" else 0.10 if use_district == "quasi_industrial" else 0.20
                
                # 用途地域に応じた動的な根拠ラベル（例: 20%+5%, 工場立地法）
                basis_label = f"{int(r_green*100)}%+5%, 工場立地法"
                green_reqs[basis_label] = site_area * r_green
            
            # 最も厳しい（面積が大きい）基準を抽出
            max_basis = max(green_reqs, key=green_reqs.get)
            max_green = green_reqs[max_basis]

        # --- 📐 調整池概算容量計算（変数名同期・不要時ガード版） ---
        # 💡 report_data でエラーを出さないよう、最初に行の先頭で初期値を定義しておきます
        vol_min, vol_max = 0.0, 0.0
        
        if site_area < 1000.0:
            pond_volume_str = "不要"
        else:
            # 1. 面積を㎡からhaに換算 (A1)
            A1 = site_area / 10000.0
            
            # 2. 面積に応じてαを判定
            alpha = 2 if A1 >= 2.0 else 1
            
            # 3. 各種固定変数の定義
            ri = 122
            f1 = 0.9
            rc = 28
            f2 = 0.6
            t1 = 30
            
            # 4. 通常の容量計算式の実行（基本の容量）
            pond_volume_base = ( (ri * f1) - ((rc / 2) * f2) ) * alpha * t1 * 60 * A1 * (1 / 360)
            
            import math
            
            # 5. 巴川流域（is_tomoe が True）の場合の処理
            if is_tomoe:
                # 面積（A1）に応じて係数（factor）を自動計算（0.1haで1.1 〜 1.5haで1.3 の直線補間）
                if A1 <= 0.1:
                    factor = 1.1
                elif A1 >= 1.5:
                    factor = 1.3
                else:
                    factor = 1.1 + ((A1 - 0.1) * (0.2 / 1.4))
                
                # 💡 変数名を v_min / v_max から vol_min / vol_max に変更し、辞書側と同期させます
                vol_min = math.ceil(pond_volume_base / 10) * 10
                vol_max = math.ceil((pond_volume_base * factor) / 10) * 10
                
                # 「通常値 〜 上限値」の幅を持たせた文字列にする
                pond_volume_str = f"{vol_min:,} ～ {vol_max:,}㎥"
                
            else:
                # 通常の場合（今まで通り単一の10の倍数切り上げ）
                pond_volume_rounded = math.ceil(pond_volume_base / 10) * 10
                pond_volume_str = f"{pond_volume_rounded:,}㎥"
                
                # 通常エリアの場合も、辞書用に値をセットしておく（必要に応じて流用可能）
                vol_min = pond_volume_rounded
                vol_max = pond_volume_rounded

        # --- 🌳 緩衝帯判定 ---
        buffer_zone_status = "不要"  # 初期値
        
        # 💡 is_point_modeの代わりに geom_type を直接見て判定します
        area_ha = site_area / 10000.0 if geom_type != "Point" else 0.0

        if area_ha < 1.0:
            buffer_zone_status = "不要"
        elif 1.0 <= area_ha < 1.5:
            buffer_zone_status = "4m以上"
        elif 1.5 <= area_ha < 5.0:
            buffer_zone_status = "5m以上"
        elif 5.0 <= area_ha < 15.0:
            buffer_zone_status = "10m以上"
        elif 15.0 <= area_ha < 25.0:
            buffer_zone_status = "15m以上"
        else:  # 25.0ha <= area_ha
            buffer_zone_status = "20m以上"

        center_lon = user_geom.centroid.x if 'user_geom' in locals() else None
        center_lat = user_geom.centroid.y if 'user_geom' in locals() else None

        report_data = {
            # --- 📋 1. 基本情報 ---
            "input_mode": input_mode,
            "geom_type": geom_type,
            "center_lat": center_lat,
            "center_lon": center_lon,
            "loc_label": detailed_location,
            "site_area": site_area,
            "current_zone": current_zone,
            "target_use_name": use_choice,
            "kinpei_str": kinpei_str,
            "youseki_str": youseki_str,

            # --- 🛠️ 2. 各種判定ステータス（UI表示用）---
            "is_dev_required": is_dev_required,        # 開発許可
            "pond_volume_str": pond_volume_str,        # 調整池
            "agri_point_status": agri_point_status,    # 農地法
            "forest_point_status": forest_point_status,# 森林法
            "dosha_point_status": dosha_point_status,  # 土砂災害
            "road_point_status": road_point_status,    # 都市計画道路
            "cultural_point_status": cultural_point_status, # 埋蔵文化財
            "buffer_zone_status": buffer_zone_status,  # 緩衝帯
            "flood_status": flood_status,              # 洪水浸水想定
            "river_dist_status": river_dist_status,    # 河川距離

            # --- ⚠️ 3. 特殊・その他（データ不足時のキャプション用など）---
            "gdf_dosha_none": (gdf_dosha is None),     # データ未読込時の警告用
            "check_morido": check_morido,              # 盛土
            "morido_required": morido_required,        # 盛土
            
            # --- 🗺️ 4. 巴川（Place Name Master / 面積率等でまだ裏で使う場合）---
            # ※もし巴川周辺の計算UI等で完全に使わなくなっていれば、後からさらに削れます
            "is_tomoe": is_tomoe,
            "vol_min": vol_min,
            "vol_max": vol_max,
            "purpose_none": (selected_purpose is None),
            "max_basis": max_basis,
            "max_green": max_green,

            # --- 📄 5. PDF出力用バックアップ ---
            # ※もしPDF出力側で直撃面積などを参照している場合は残しておく必要があります
            "dosha_red_area": dosha_red_area,
            "dosha_yellow_area": dosha_yellow_area,
        }

        # === ✨【修正版】建蔽率と容積率をいかなる場合も必ずペアで結合する処理 ===
        k_list = [x.strip() for x in report_data.get('kinpei_str', '').split(',') if x.strip()]
        y_list = [x.strip() for x in report_data.get('youseki_str', '').split(',') if x.strip()]

        if k_list and y_list:
            # 片方の数値が同じで省略されてリスト数が合わない場合の補正
            if len(y_list) == 1 and len(k_list) > 1:
                y_list = y_list * len(k_list)
            elif len(k_list) == 1 and len(y_list) > 1:
                k_list = k_list * len(y_list)
                
            spec_pairs = []
            for k, y in zip(k_list, y_list):
                # すでに「%」や「指定なし」が入っているかチェックしながら整形
                k_val = k if "%" in k or k == "指定なし" else f"{k}%"
                y_val = y if "%" in y or y == "指定なし" else f"{y}%"
                
                # 両方とも「指定なし」なら「指定なし」だけにする
                if k_val == "指定なし" and y_val == "指定なし":
                    spec_pairs.append("指定なし")
                else:
                    spec_pairs.append(f"{k_val} / {y_val}")
            
            # 重複があれば一意にしてカンマで繋ぐ（例: 都市計画区域外で「指定なし」が重複した時などの対策）
            # リストの順序を維持したまま重複を排除します
            seen = set()
            unique_pairs = [x for x in spec_pairs if not (x in seen or seen.add(x))]
            report_data['combined_spec_str'] = ", ".join(unique_pairs)
        else:
            report_data['combined_spec_str'] = '―'
            report_data['combined_spec_str'] = '―'

    with col_btn:
        if has_data:
            if st.button("**判定**", type="primary", use_container_width=True, key="btn_pure_bold_wide"):
                show_result_dialog(report_data)
