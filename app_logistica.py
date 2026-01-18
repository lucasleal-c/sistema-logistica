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

# --- CONFIGURAÇÃO DA PÁGINA ---
st.set_page_config(page_title="Roteirizador Noroeste Paulista", layout="wide", page_icon="🚚")

# Estilo para evitar piscar tela
st.markdown("""
<style>
    .stApp {opacity: 1 !important; transition: none !important;}
    header {visibility: hidden;}
    .main .block-container {padding-top: 2rem;}
</style>
""", unsafe_allow_html=True)

# --- MEMÓRIA DO SISTEMA ---
if 'rota_calculada' not in st.session_state: st.session_state.rota_calculada = False
if 'df_final' not in st.session_state: st.session_state.df_final = None
if 'link_maps' not in st.session_state: st.session_state.link_maps = ""

# --- CHAVE DE API ---
if "CHAVE_GOOGLE" in st.secrets:
    CHAVE_GOOGLE = st.secrets["CHAVE_GOOGLE"]
else:
    CHAVE_GOOGLE = "" 

# --- FUNÇÕES ---

def extrair_endereco_pdf(arquivo):
    """Extrai endereço de notas fiscais em PDF"""
    try:
        with pdfplumber.open(arquivo) as pdf:
            texto = ""
            for page in pdf.pages:
                texto += page.extract_text() + "\n"
        
        texto_limpo = texto.replace("\n", " ")
        # Regex ajustada para capturar padrões comuns de endereço
        padroes = [
            r"(Rua|Av\.|Avenida|Travessa|Alameda|Estrada|Rodovia|Praça|Via)\s+[^,]+,\s*\d+", 
            r"Endereço:\s*([^,]+,\s*\d+)",
            r"Entrega:\s*([^,]+,\s*\d+)"
        ]
        for padrao in padroes:
            match = re.search(padrao, texto_limpo, re.IGNORECASE)
            if match:
                return match.group(0).replace("Endereço:", "").replace("Entrega:", "").strip()
    except:
        return None
    return None

def obter_lat_long_google(endereco):
    """Converte endereço em coordenadas com filtro de erro"""
    try:
        geolocator = GoogleV3(api_key=CHAVE_GOOGLE)
        # Garante busca no Brasil
        busca = endereco if "Brasil" in endereco else f"{endereco}, Brasil"
        
        local = geolocator.geocode(busca)
        
        if local:
            # --- FILTRO ANTI-MATO GROSSO ---
            # O Google joga endereços não achados para (-14.235, -51.925)
            # Vamos bloquear coordenadas próximas a isso.
            lat_erro = -14.235
            lon_erro = -51.925
            
            diferenca_lat = abs(local.latitude - lat_erro)
            diferenca_lon = abs(local.longitude - lon_erro)

            # Se for muito perto do "centro geográfico genérico", ignora
            if diferenca_lat < 0.5 and diferenca_lon < 0.5:
                return None, None, "Local Genérico (Mato Grosso - Erro)"
                
            return local.latitude, local.longitude, local.address
    except Exception as e:
        return None, None, f"Erro API: {str(e)}"
    return None, None, "Não encontrado"

def obter_distancia_rodagem(lat1, lon1, lat2, lon2):
    """Calcula distância via estrada (OSRM) ou linha reta se falhar"""
    loc = f"{lon1},{lat1};{lon2},{lat2}"
    url = f"http://router.project-osrm.org/route/v1/driving/{loc}?overview=false"
    headers = {"User-Agent": "SistemaLogistica/1.0"}
    try:
        r = requests.get(url, headers=headers, timeout=2)
        if r.status_code == 200: return r.json()['routes'][0]['distance']
    except: pass
    return geodesic((lat1, lon1), (lat2, lon2)).meters

def pegar_trajeto_desenho(lat1, lon1, lat2, lon2):
    """Pega os pontos para desenhar a linha azul na estrada"""
    loc = f"{lon1},{lat1};{lon2},{lat2}"
    url = f"http://router.project-osrm.org/route/v1/driving/{loc}?overview=full&geometries=polyline"
    headers = {"User-Agent": "SistemaLogistica/1.0"}
    try:
        r = requests.get(url, headers=headers, timeout=2)
        if r.status_code == 200:
            return polyline.decode(r.json()['routes'][0]['geometry'])
    except: pass
    return [(lat1, lon1), (lat2, lon2)]

# --- MENU LATERAL ---
st.sidebar.header("⚙️ Painel de Controle")
modo_rota = st.sidebar.radio("Estratégia:", ("Modo Econômico", "Modo Caminhoneiro"))
st.sidebar.info(
    "**Econômico:** Vai sempre para o vizinho mais perto.\n"
    "**Caminhoneiro:** Vai na entrega mais longe primeiro para vir voltando."
)
st.sidebar.markdown("---")
consumo = st.sidebar.number_input("Consumo (km/L):", value=3.5, step=0.1, help="Média de consumo do caminhão")
