import streamlit as st
import pandas as pd
import folium
import requests
import polyline
from streamlit_folium import st_folium
from geopy.geocoders import GoogleV3
from geopy.distance import geodesic
import time
import pdfplumber
import re

# --- CONFIGURAÇÃO INICIAL E GAMBIARRA VISUAL ---
st.set_page_config(page_title="Sistema de Rotas Inteligente", layout="wide", page_icon="🚚")

st.markdown("""
<style>
    .stApp {opacity: 1 !important; transition: none !important;}
    header {visibility: hidden;}
    .main .block-container {padding-top: 2rem;}
</style>
""", unsafe_allow_html=True)

# --- MEMÓRIA (SESSION STATE) ---
if 'rota_calculada' not in st.session_state: st.session_state.rota_calculada = False
if 'df_final' not in st.session_state: st.session_state.df_final = None
if 'link_maps' not in st.session_state: st.session_state.link_maps = ""

# --- SEGURANÇA DA CHAVE ---
if "CHAVE_GOOGLE" in st.secrets:
    CHAVE_GOOGLE = st.secrets["CHAVE_GOOGLE"]
else:
    CHAVE_GOOGLE = "" 

# --- FUNÇÕES ---
def extrair_endereco_pdf(arquivo):
    """Tenta encontrar um endereço dentro do texto do PDF"""
    with pdfplumber.open(arquivo) as pdf:
        texto = ""
        for page in pdf.pages:
            texto += page.extract_text() + "\n"
    
    # 1. Limpeza básica
    texto_limpo = texto.replace("\n", " ")
    
    # 2. PADRÕES DE ENDEREÇO (REGEX)
    # Procura por: (Rua/Av/Etc) + (Nome) + , + (Número) - opcional + (Bairro/Cidade)
    padroes = [
        r"(Rua|Av\.|Avenida|Travessa|Alameda|Estrada|Rodovia)\s+[^,]+,\s*\d+", # Padrão: Rua X, 123
        r"Endereço:\s*([^,]+,\s*\d+)", # Padrão: Endereço: Rua X, 123
        r"Entrega:\s*([^,]+,\s*\d+)"   # Padrão: Entrega: Rua X, 123
    ]
    
    for padrao in padroes:
        match = re.search(padrao, texto_limpo, re.IGNORECASE)
        if match:
            # Se achou, retorna o texto encontrado (ex: Rua das Flores, 123)
            return match.group(0).replace("Endereço:", "").replace("Entrega:", "").strip()
            
    return None

def obter_lat_long_google(endereco):
    try:
        geolocator = GoogleV3(api_key=CHAVE_GOOGLE)
        # Adiciona Brasil para garantir
        local = geolocator.geocode(f"{endereco}, Brasil")
        if local:
            return local.latitude, local.longitude, local.address
    except:
        return None, None, None
    return None, None, None

def obter_distancia_rodagem(lat1, lon1, lat2, lon2):
    loc = f"{lon1},{lat1};{lon2},{lat2}"
    url = f"http://router.project-osrm.org/route/v1/driving/{loc}?overview=false"
    headers = {"User-Agent": "SistemaLogistica/1.0"}
    try:
        r = requests.get(url, headers=headers, timeout=5)
        if r.status_code == 200: return r.json()['routes'][0]['distance']
    except: pass
    return geodesic((lat1, lon1), (lat2, lon2)).meters

def pegar_trajeto_desenho(lat1, lon1, lat2, lon2):
    loc = f"{lon1},{lat1};{lon2},{lat2}"
    url = f"http://router.project-osrm.org/route/v1/driving/{loc}?overview=full&geometries=polyline"
    headers = {"User-Agent": "SistemaLogistica/1.0"}
    try:
        r = requests.get(url, headers=headers, timeout=5)
        if r.status_code == 200:
            return polyline.decode(r.json()['routes'][0]['geometry'])
    except: pass
    return [(lat1, lon1), (lat2, lon2)]

# --- INTERFACE ---
st.title("🚚 Leitor de Notas & Roteirizador")
st.markdown("---")

if not st.session_state.rota_calculada:
    st.info("📂 **Opção 1:** Arraste seus **PDFs (Notas Fiscais)** ou **Excel** abaixo.")
    
    # ACEITA MÚLTIPLOS ARQUIVOS (PDF ou Excel)
    arquivos_carregados = st.file_uploader(
        "Upload dos arquivos", 
        type=["pdf", "xlsx"], 
        accept_multiple_files=True
    )

    if st.button("🚀 LER ARQUIVOS E GERAR ROTA"):
        if not arquivos_carregados:
            st.error("⚠️ Nenhum arquivo enviado!")
            st.stop()
        if CHAVE_GOOGLE == "":
            st.error("⚠️ Erro: Chave do Google não configurada!")
            st.stop()

        bar = st.progress(0)
        status = st.empty()
        enderecos_encontrados = []

        # --- PROCESSAMENTO DOS ARQUIVOS ---
        total_arquivos = len(arquivos_carregados)
        
        for idx, arquivo in enumerate(arquivos_carregados):
            status.text(f"Lendo arquivo {idx+1}/{total_arquivos}: {arquivo.name}...")
            
            if arquivo.name.endswith(".xlsx"):
                # Se for Excel, lê normal
                df_temp = pd.read_excel(arquivo)
                if "Latitude" not in df_temp.columns: df_temp["Latitude"] = None
                if "Longitude" not in df_temp.columns: df_temp["Longitude"] = None
                
                # Tenta achar colunas de endereço
                for i, row in df_temp.iterrows():
                    if pd.notna(row.get('Latitude')):
                        enderecos_encontrados.append({'Rua': f"Cliente {i}", 'Latitude': row['Latitude'], 'Longitude': row['Longitude']})
                    else:
                        # Monta string de busca
                        cols_end = [str(row[c]) for c in df_temp.columns if c in ['Rua', 'Logradouro', 'Endereco', 'Cidade', 'Numero']]
                        end_str = ", ".join(cols_end)
                        enderecos_encontrados.append({'Rua': end_str, 'Latitude': None, 'Longitude': None})

            elif arquivo.name.endswith(".pdf"):
                # Se for PDF, tenta extrair o texto
                end_extraido = extrair_endereco_pdf(arquivo)
                if end_extraido:
                    enderecos_encontrados.append({'Rua': end_extraido, 'Latitude': None, 'Longitude': None, 'Arquivo': arquivo.name})
                else:
                    st.warning(f"⚠️ Não achei endereço no PDF: {arquivo.name}")

            bar.progress((idx + 1) / total_arquivos * 0.3)

        # Transforma em DataFrame
        df = pd.DataFrame(enderecos_encontrados)
        if df.empty:
            st.error("❌ Nenhum endereço encontrado nos arquivos.")
            st.stop()

        # --- GEOCODIFICAÇÃO ---
        status.text("Buscando coordenadas no Google...")
        for i, row in df.iterrows():
            if pd.isna(row["Latitude"]):
                lat, lon, end_google = obter_lat_long_google(row["Rua"])
                if lat:
                    df.at[i, "Latitude"] = lat
                    df.at[i, "Longitude"] = lon
                    df.at[i, "Rua"] = end_google # Atualiza com o nome oficial do Google
                time.sleep(0.1)
            bar.progress(0.3 + ((i + 1) / len(df) * 0.3))
        
        df = df.dropna(subset=['Latitude'])
        if df.empty:
            st.error("❌ Não consegui localizar nenhum endereço no mapa.")
            st.stop()

        # --- OTIMIZAÇÃO DE ROTA (Vizinho mais próximo) ---
        status.text("Otimizando logística...")
        base = df.iloc[0] # Assume o primeiro como base/partida
        clientes = df.iloc[1:].copy()
        rota_ordenada = [base]
        posicao_atual = base
        
        passos = 0
        qtd_inicial = len(clientes)
        
        while not clientes.empty:
            dist_min = float('inf')
            idx_min = -1
            for i, c in clientes.iterrows():
                d = obter_distancia_rodagem(posicao_atual['Latitude'], posicao_atual['Longitude'], c['Latitude'], c['Longitude'])
                if d < dist_min: dist_min = d; idx_min = i
            
            if idx_min != -1:
                prox = clientes.loc[idx_min]
                rota_ordenada.append(prox)
                posicao_atual = prox
                clientes = clientes.drop(idx_min)
                time.sleep(0.1)
            else: break
            passos += 1
            bar.progress(0.6 + (passos / qtd_inicial * 0.3))

        # --- SALVAR E FINALIZAR ---
        df_final = pd.DataFrame(rota_ordenada)
        st.session_state.df_final = df_final
        
        # Link Maps
        base_url = "https://www.google.com/maps/dir/"
        coords_list = [f"{row['Latitude']},{row['Longitude']}" for _, row in df_final.iterrows()]
        coords_list.append(f"{df_final.iloc[0]['Latitude']},{df_final.iloc[0]['Longitude']}") # Volta pra base
        st.session_state.link_maps = base_url + "/".join(coords_list)
        
        st.session_state.rota_calculada = True
        bar.progress(1.0)
        time.sleep(0.5)
        st.rerun()

# --- EXIBIÇÃO ---
if st.session_state.rota_calculada and st.session_state.df_final is not None:
    col1, col2 = st.columns([3, 1])
    with col1: st.subheader("🗺️ Rota das Notas Fiscais")
    with col2:
        if st.button("🔄 Reiniciar"):
            st.session_state.rota_calculada = False
            st.session_state.df_final = None
            st.rerun()

    df_final = st.session_state.df_final
    dist_total = 0
    
    try:
        m = folium.Map(location=[df_final["Latitude"].mean(), df_final["Longitude"].mean()], zoom_start=13)
        
        for i in range(len(df_final)):
            atual = df_final.iloc[i]
            # Marcador com nome do arquivo (se tiver) ou endereço
            popup_text = f"{i}. {atual.get('Arquivo', '')} - {atual['Rua']}"
            
            folium.Marker(
                [atual['Latitude'], atual['Longitude']], 
                popup=popup_text,
                icon=folium.Icon(color="green" if i==0 else "red", icon="home" if i==0 else "file")
            ).add_to(m)
            
            if i < len(df_final) - 1:
                prox = df_final.iloc[i+1]
                time.sleep(0.4) 
                trajeto = pegar_trajeto_desenho(atual['Latitude'], atual['Longitude'], prox['Latitude'], prox['Longitude'])
                dist = obter_distancia_rodagem(atual['Latitude'], atual['Longitude'], prox['Latitude'], prox['Longitude'])
                folium.PolyLine(trajeto, color="blue", weight=5, opacity=0.8).add_to(m)
                dist_total += dist
            elif i == len(df_final) - 1: # Volta base
                base = df_final.iloc[0]
                time.sleep(0.4)
                trajeto = pegar_trajeto_desenho(atual['Latitude'], atual['Longitude'], base['Latitude'], base['Longitude'])
                dist = obter_distancia_rodagem(atual['Latitude'], atual['Longitude'], base['Latitude'], base['Longitude'])
                folium.PolyLine(trajeto, color="gray", weight=3, dash_array='5').add_to(m)
                dist_total += dist

        st_folium(m, width=900, height=500)
        st.success(f"✅ Rota Otimizada! Total: {dist_total/1000:.1f} km")
        
        st.markdown(f'''
            <a href="{st.session_state.link_maps}" target="_blank" style="text-decoration: none;">
                <button style="background-color: #4CAF50; color: white; padding: 15px 32px; border-radius: 8px; border: none; width: 100%; cursor: pointer; font-size: 16px;">
                    🌍 <b>ABRIR NO APP (GOOGLE MAPS)</b>
                </button>
            </a>
            ''', unsafe_allow_html=True)
            
    except Exception as e: st.error(f"Erro mapa: {e}")
