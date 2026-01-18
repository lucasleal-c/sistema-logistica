import streamlit as st
import pandas as pd
import folium
import requests
import polyline
from streamlit_folium import st_folium
from geopy.geocoders import GoogleV3
from geopy.distance import geodesic
import time

# --- CONFIGURAÇÃO INICIAL ---
st.set_page_config(page_title="Sistema de Rotas Inteligente", layout="wide")

# --- SEGURANÇA DA CHAVE (CLOUD) ---
if "CHAVE_GOOGLE" in st.secrets:
    CHAVE_GOOGLE = st.secrets["CHAVE_GOOGLE"]
else:
    CHAVE_GOOGLE = "" # Deixa vazio para segurança no GitHub

# --- FUNÇÕES ---
def obter_lat_long_google(endereco):
    try:
        geolocator = GoogleV3(api_key=CHAVE_GOOGLE)
        local = geolocator.geocode(endereco)
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
        r = requests.get(url, headers=headers, timeout=3)
        if r.status_code == 200:
            return r.json()['routes'][0]['distance']
    except:
        pass
    return geodesic((lat1, lon1), (lat2, lon2)).meters

def pegar_trajeto_desenho(lat1, lon1, lat2, lon2):
    loc = f"{lon1},{lat1};{lon2},{lat2}"
    url = f"http://router.project-osrm.org/route/v1/driving/{loc}?overview=full&geometries=polyline"
    headers = {"User-Agent": "SistemaLogistica/1.0"}
    try:
        r = requests.get(url, headers=headers, timeout=3)
        res = r.json()
        return polyline.decode(res['routes'][0]['geometry'])
    except:
        return [(lat1, lon1), (lat2, lon2)]

# --- INTERFACE VISUAL ---
st.title("🚚 Otimizador de Rotas e Logística")
st.markdown("---")

# 1. BARRA LATERAL
st.sidebar.header("⚙️ Configurações")
modo_rota = st.sidebar.radio("Estratégia:", ("Modo Econômico", "Modo Caminhoneiro"))
st.sidebar.markdown("---")
consumo = st.sidebar.number_input("Consumo (km/L)", value=8.0)
preco_gas = st.sidebar.number_input("Preço Diesel (R$)", value=5.89)

# 2. UPLOAD DE ARQUIVO (AQUI ESTÁ A MÁGICA PARA A NUVEM!)
st.info("📂 Passo 1: Arraste seu arquivo Excel abaixo")
arquivo_carregado = st.file_uploader("Upload do arquivo de Clientes", type=["xlsx"])

if st.button("🚀 GERAR ROTA AGORA"):
    
    if arquivo_carregado is None:
        st.error("⚠️ Você precisa enviar um arquivo Excel antes!")
        st.stop()
        
    if CHAVE_GOOGLE == "":
        st.error("⚠️ Erro de Segurança: Chave do Google não encontrada nos Secrets!")
        st.stop()

    bar = st.progress(0)
    status_text = st.empty()
    
    try:
        status_text.text("Lendo Excel...")
        df = pd.read_excel(arquivo_carregado)
        
        if "Latitude" not in df.columns: df["Latitude"] = None
        if "Longitude" not in df.columns: df["Longitude"] = None
        
        status_text.text("Consultando Google Maps...")
        for i, row in df.iterrows():
            lat_val = row["Latitude"]
            if pd.isna(lat_val) or str(lat_val).strip() == "":
                end_completo = f"{row['Rua']}, {row['Numero']} - {row['Cidade']}, Brasil"
                lat, lon, end_google = obter_lat_long_google(end_completo)
                if lat:
                    df.at[i, "Latitude"] = lat
                    df.at[i, "Longitude"] = lon
            bar.progress((i + 1) / len(df) * 0.5)
        
        df["Latitude"] = pd.to_numeric(df["Latitude"], errors="coerce")
        df = df.dropna(subset=['Latitude'])
        
        if df.empty:
            st.error("❌ Nenhum endereço válido encontrado.")
            st.stop()
            
        status_text.text("Calculando Rota...")
        base = df.iloc[0]
        clientes = df.iloc[1:].copy()
        rota_ordenada = [base]
        posicao_atual = base
        
        if modo_rota == "Modo Caminhoneiro":
            dist_max = -1
            idx_max = -1
            for i, c in clientes.iterrows():
                d = obter_distancia_rodagem(base['Latitude'], base['Longitude'], c['Latitude'], c['Longitude'])
                if d > dist_max: dist_max = d; idx_max = i
            if idx_max != -1:
                primeiro = clientes.loc[idx_max]
                rota_ordenada.append(primeiro)
                posicao_atual = primeiro
                clientes = clientes.drop(idx_max)

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
            else: break
            
            passos += 1
            bar.progress(0.5 + (passos / qtd_inicial * 0.5))
            
        # MOSTRAR MAPA
        st.subheader("🗺️ Mapa da Rota")
        df_final = pd.DataFrame(rota_ordenada)
        
        m = folium.Map(location=[df_final["Latitude"].mean(), df_final["Longitude"].mean()], zoom_start=14)
        dist_total = 0
        links = []
        
        for i in range(len(df_final)):
            atual = df_final.iloc[i]
            links.append(f"{atual['Latitude']},{atual['Longitude']}")
            icon = "home" if i == 0 else "info-sign"
            color = "green" if i == 0 else "red"
            folium.Marker([atual['Latitude'], atual['Longitude']], popup=f"{i}. {atual['Rua']}", icon=folium.Icon(color=color, icon=icon)).add_to(m)
            
            if i < len(df_final) - 1:
                prox = df_final.iloc[i+1]
                trajeto = pegar_trajeto_desenho(atual['Latitude'], atual['Longitude'], prox['Latitude'], prox['Longitude'])
                dist_total += obter_distancia_rodagem(atual['Latitude'], atual['Longitude'], prox['Latitude'], prox['Longitude'])
                folium.PolyLine(trajeto, color="blue", weight=4).add_to(m)
        
        st_folium(m, width=900, height=500)
        st.success(f"Rota Gerada! Distância: {dist_total/1000:.1f} km")
        status_text.empty()
        bar.empty()

    except Exception as e:
        st.error(f"Erro: {e}")
