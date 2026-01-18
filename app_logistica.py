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

# COLE SUA CHAVE DO GOOGLE AQUI
CHAVE_GOOGLE = "AIzaSyBz4-MkVqZFX1N_GcIEhHwcHl_CH0PtAHE" 

# --- INICIALIZAÇÃO DA MEMÓRIA (SESSION STATE) ---
# Isso impede que o site "esqueça" a rota quando você mexe no mapa
if 'rota_calculada' not in st.session_state:
    st.session_state.rota_calculada = False
if 'df_final' not in st.session_state:
    st.session_state.df_final = None

# --- FUNÇÕES ---

def obter_lat_long_google(endereco):
    """Usa o Google para achar lat/long exato"""
    try:
        geolocator = GoogleV3(api_key=CHAVE_GOOGLE)
        local = geolocator.geocode(endereco)
        if local:
            return local.latitude, local.longitude, local.address
    except:
        return None, None, None
    return None, None, None

def obter_distancia_rodagem(lat1, lon1, lat2, lon2):
    """Pergunta ao OSRM a distância dirigindo (com cabeçalho para evitar bloqueio)"""
    loc = f"{lon1},{lat1};{lon2},{lat2}"
    url = f"http://router.project-osrm.org/route/v1/driving/{loc}?overview=false"
    headers = {"User-Agent": "SistemaLogistica/1.0"} # Identidade para não ser bloqueado
    
    try:
        r = requests.get(url, headers=headers, timeout=3)
        if r.status_code == 200:
            return r.json()['routes'][0]['distance'] # Metros
    except:
        pass
    # Se falhar, usa linha reta
    return geodesic((lat1, lon1), (lat2, lon2)).meters

def pegar_trajeto_desenho(lat1, lon1, lat2, lon2):
    """Pega o desenho da linha azul da rua"""
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

modo_rota = st.sidebar.radio(
    "Estratégia de Rota:",
    ("Modo Econômico (Vizinho mais próximo)", 
     "Modo Caminhoneiro (Começar do mais longe)")
)

st.sidebar.markdown("---")
consumo = st.sidebar.number_input("Consumo (km/L)", value=8.0, step=0.5)
preco_gas = st.sidebar.number_input("Preço Diesel (R$)", value=5.89, step=0.01)

st.info("Passo 1: Certifique-se que o arquivo 'clientes.xlsx' está na pasta.")

# O botão agora apenas ATIVA o processamento, mas não segura a tela sozinho
if st.button("🚀 GERAR ROTA AGORA"):
    
    # ---------------- INICIO DO PROCESSO ----------------
    bar = st.progress(0)
    status_text = st.empty()
    
    try:
        status_text.text("Lendo Excel...")
        df = pd.read_excel("clientes.xlsx")
        
        # Garante colunas
        if "Latitude" not in df.columns: df["Latitude"] = None
        if "Longitude" not in df.columns: df["Longitude"] = None
        
        # --- GEOCODING ---
        status_text.text("Consultando endereços no Google...")
        
        for i, row in df.iterrows():
            lat_val = row["Latitude"]
            # Se for vazio ou NaN
            if pd.isna(lat_val) or str(lat_val).strip() == "":
                end_completo = f"{row['Rua']}, {row['Numero']} - {row['Cidade']}, Brasil"
                lat, lon, end_google = obter_lat_long_google(end_completo)
                
                if lat:
                    df.at[i, "Latitude"] = lat
                    df.at[i, "Longitude"] = lon
            
            bar.progress((i + 1) / len(df) * 0.5) # Vai até 50%
        
        # Limpeza
        df["Latitude"] = pd.to_numeric(df["Latitude"], errors="coerce")
        df = df.dropna(subset=['Latitude'])
        
        if df.empty:
            st.error("Nenhum endereço válido encontrado.")
            st.stop()
            
        # --- OTIMIZAÇÃO ---
        status_text.text("Calculando melhor rota de entrega...")
        
        base = df.iloc[0]
        clientes = df.iloc[1:].copy()
        rota_ordenada = [base]
        posicao_atual = base
        
        # Modo Caminhoneiro
        if modo_rota == "Modo Caminhoneiro (Começar do mais longe)":
            dist_max = -1
            idx_max = -1
            for i, c in clientes.iterrows():
                d = obter_distancia_rodagem(base['Latitude'], base['Longitude'], c['Latitude'], c['Longitude'])
                if d > dist_max:
                    dist_max = d
                    idx_max = i
            
            if idx_max != -1:
                primeiro = clientes.loc[idx_max]
                rota_ordenada.append(primeiro)
                posicao_atual = primeiro
                clientes = clientes.drop(idx_max)

        # Vizinho mais próximo
        qtd_inicial = len(clientes)
        passos = 0
        while not clientes.empty:
            dist_min = float('inf')
            idx_min = -1
            
            for i, c in clientes.iterrows():
                d = obter_distancia_rodagem(posicao_atual['Latitude'], posicao_atual['Longitude'], c['Latitude'], c['Longitude'])
                if d < dist_min:
                    dist_min = d
                    idx_min = i
            
            if idx_min != -1:
                prox = clientes.loc[idx_min]
                rota_ordenada.append(prox)
                posicao_atual = prox
                clientes = clientes.drop(idx_min)
            else:
                break
            
            passos += 1
            # Atualiza barra do 50% até 100%
            progresso_atual = 0.5 + (passos / qtd_inicial * 0.5)
            if progresso_atual > 1.0: progresso_atual = 1.0
            bar.progress(progresso_atual)
            time.sleep(0.1) # Respira pro OSRM não travar
            
        # --- SALVA NA MEMÓRIA DO SITE ---
        st.session_state.df_final = pd.DataFrame(rota_ordenada)
        st.session_state.rota_calculada = True
        
        status_text.text("Concluído!")
        bar.empty()
        st.rerun() # Recarrega a página para mostrar o mapa fixo

    except Exception as e:
        st.error(f"Ocorreu um erro: {e}")
        st.session_state.rota_calculada = False

# ---------------- EXIBIÇÃO DO RESULTADO (FORA DO BOTÃO) ----------------
# Essa parte roda sempre que a 'rota_calculada' for Verdadeira

if st.session_state.rota_calculada and st.session_state.df_final is not None:
    
    df_final = st.session_state.df_final
    st.subheader("🗺️ Mapa da Rota Otimizada")
    
    # Mapa
    try:
        media_lat = df_final["Latitude"].mean()
        media_long = df_final["Longitude"].mean()
        m = folium.Map(location=[media_lat, media_long], zoom_start=14)
        
        dist_total = 0
        links = []
        
        for i in range(len(df_final)):
            atual = df_final.iloc[i]
            links.append(f"{atual['Latitude']},{atual['Longitude']}")
            
            # Marcador
            icon_name = "home" if i == 0 else "info-sign"
            color_name = "green" if i == 0 else "red"
            
            folium.Marker(
                [atual['Latitude'], atual['Longitude']],
                popup=f"{i}. {atual['Rua']}",
                icon=folium.Icon(color=color_name, icon=icon_name),
                tooltip=f"{i}. {atual['Rua']}"
            ).add_to(m)
            
            # Linha até o próximo
            if i < len(df_final) - 1:
                prox = df_final.iloc[i+1]
                trajeto = pegar_trajeto_desenho(atual['Latitude'], atual['Longitude'], prox['Latitude'], prox['Longitude'])
                dist = obter_distancia_rodagem(atual['Latitude'], atual['Longitude'], prox['Latitude'], prox['Longitude'])
                folium.PolyLine(trajeto, color="blue", weight=4).add_to(m)
                dist_total += dist
            
            # Volta pra base
            elif i == len(df_final) - 1:
                base = df_final.iloc[0]
                trajeto = pegar_trajeto_desenho(atual['Latitude'], atual['Longitude'], base['Latitude'], base['Longitude'])
                dist = obter_distancia_rodagem(atual['Latitude'], atual['Longitude'], base['Latitude'], base['Longitude'])
                folium.PolyLine(trajeto, color="gray", dash_array='5').add_to(m)
                dist_total += dist
                links.append(f"{base['Latitude']},{base['Longitude']}")

        st_folium(m, width=900, height=500)
        
        # Métricas
        km = dist_total / 1000
        litros = km / consumo
        valor = litros * preco_gas
        
        c1, c2, c3 = st.columns(3)
        c1.metric("Distância", f"{km:.1f} km")
        c2.metric("Diesel", f"{litros:.1f} L")
        c3.metric("Custo", f"R$ {valor:.2f}")
        
        link_final = "https://www.google.com/maps/dir/" + "/".join(links)
        st.markdown(f"[**Abrir Rota no Google Maps**]({link_final})")
        
        if st.button("🔄 Calcular Nova Rota"):
            st.session_state.rota_calculada = False
            st.rerun()
            
    except Exception as e:
        st.error(f"Erro ao desenhar mapa: {e}")