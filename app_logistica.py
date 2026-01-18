import streamlit as st
import pandas as pd
import folium
import requests
import polyline
from streamlit_folium import st_folium
from geopy.geocoders import GoogleV3
from geopy.distance import geodesic
import time

# --- CONFIGURAÇÃO INICIAL E GAMBIARRA VISUAL ---
st.set_page_config(page_title="Sistema de Rotas Inteligente", layout="wide", page_icon="🚚")

# CSS para impedir que a tela pisque/escureça
st.markdown("""
<style>
    .stApp {
        opacity: 1 !important; 
        transition: none !important;
    }
    header {visibility: hidden;}
    .main .block-container {
        padding-top: 2rem;
    }
</style>
""", unsafe_allow_html=True)

# --- MEMÓRIA (SESSION STATE) ---
if 'rota_calculada' not in st.session_state:
    st.session_state.rota_calculada = False
if 'df_final' not in st.session_state:
    st.session_state.df_final = None
if 'link_maps' not in st.session_state:
    st.session_state.link_maps = ""

# --- SEGURANÇA DA CHAVE ---
if "CHAVE_GOOGLE" in st.secrets:
    CHAVE_GOOGLE = st.secrets["CHAVE_GOOGLE"]
else:
    CHAVE_GOOGLE = "" 

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
    # Tenta pegar a distancia real de carro
    loc = f"{lon1},{lat1};{lon2},{lat2}"
    url = f"http://router.project-osrm.org/route/v1/driving/{loc}?overview=false"
    headers = {"User-Agent": "SistemaLogistica/1.0"}
    try:
        r = requests.get(url, headers=headers, timeout=5)
        if r.status_code == 200:
            return r.json()['routes'][0]['distance']
    except:
        pass
    return geodesic((lat1, lon1), (lat2, lon2)).meters

def pegar_trajeto_desenho(lat1, lon1, lat2, lon2):
    # Tenta pegar a geometria (o desenho da estrada)
    loc = f"{lon1},{lat1};{lon2},{lat2}"
    url = f"http://router.project-osrm.org/route/v1/driving/{loc}?overview=full&geometries=polyline"
    headers = {"User-Agent": "SistemaLogistica/1.0"}
    try:
        r = requests.get(url, headers=headers, timeout=5)
        if r.status_code == 200:
            res = r.json()
            return polyline.decode(res['routes'][0]['geometry'])
    except:
        pass
    # Se falhar, retorna linha reta como fallback
    return [(lat1, lon1), (lat2, lon2)]

# --- INTERFACE ---
st.title("🚚 Otimizador de Rotas e Logística")
st.markdown("---")

st.sidebar.header("⚙️ Configurações")
modo_rota = st.sidebar.radio("Estratégia:", ("Modo Econômico", "Modo Caminhoneiro"))
st.sidebar.markdown("---")

# Se NÃO tiver rota calculada, mostra o upload
if not st.session_state.rota_calculada:
    st.info("📂 Passo 1: Arraste seu arquivo Excel abaixo")
    arquivo_carregado = st.file_uploader("Upload do arquivo de Clientes", type=["xlsx"])

    if st.button("🚀 GERAR ROTA AGORA"):
        
        if arquivo_carregado is None:
            st.error("⚠️ Você precisa enviar um arquivo Excel antes!")
            st.stop()
        if CHAVE_GOOGLE == "":
            st.error("⚠️ Erro: Chave do Google não configurada nos Secrets!")
            st.stop()

        bar = st.progress(0)
        status_text = st.empty()
        
        try:
            status_text.text("Lendo Excel...")
            df = pd.read_excel(arquivo_carregado)
            
            if "Latitude" not in df.columns: df["Latitude"] = None
            if "Longitude" not in df.columns: df["Longitude"] = None
            
            status_text.text("Geocodificando endereços...")
            for i, row in df.iterrows():
                lat_val = row["Latitude"]
                if pd.isna(lat_val) or str(lat_val).strip() == "":
                    end_completo = f"{row['Rua']}, {row['Numero']} - {row['Cidade']}, Brasil"
                    lat, lon, end_google = obter_lat_long_google(end_completo)
                    if lat:
                        df.at[i, "Latitude"] = lat
                        df.at[i, "Longitude"] = lon
                    time.sleep(0.1) # Pequena pausa para o Google não bloquear
                bar.progress((i + 1) / len(df) * 0.4)
            
            df["Latitude"] = pd.to_numeric(df["Latitude"], errors="coerce")
            df = df.dropna(subset=['Latitude'])
            
            if df.empty:
                st.error("❌ Nenhum endereço válido encontrado.")
                st.stop()
                
            status_text.text("Calculando a melhor logística...")
            base = df.iloc[0]
            clientes = df.iloc[1:].copy()
            rota_ordenada = [base]
            posicao_atual = base
            
            # Lógica de Ordenação
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
                bar.progress(0.4 + (passos / qtd_inicial * 0.4))
                time.sleep(0.2) # Pausa leve para não sobrecarregar o roteador
            
            # --- SALVAR RESULTADOS NA MEMÓRIA ---
            df_final = pd.DataFrame(rota_ordenada)
            st.session_state.df_final = df_final
            
            # Criar Link do Google Maps
            base_url = "https://www.google.com/maps/dir/"
            coords_list = []
            for _, row in df_final.iterrows():
                coords_list.append(f"{row['Latitude']},{row['Longitude']}")
            
            # Adicionar volta para casa
            coords_list.append(f"{df_final.iloc[0]['Latitude']},{df_final.iloc[0]['Longitude']}")
            
            st.session_state.link_maps = base_url + "/".join(coords_list)
            st.session_state.rota_calculada = True
            
            status_text.text("Desenhando mapa...")
            bar.progress(1.0)
            time.sleep(0.5)
            st.rerun()

        except Exception as e:
            st.error(f"Erro: {e}")
            st.session_state.rota_calculada = False

# --- EXIBIÇÃO DO RESULTADO ---
if st.session_state.rota_calculada and st.session_state.df_final is not None:
    
    col1, col2 = st.columns([3, 1])
    with col1:
        st.subheader("🗺️ Mapa da Rota Real")
    with col2:
        if st.button("🔄 Nova Rota"):
            st.session_state.rota_calculada = False
            st.session_state.df_final = None
            st.rerun()

    df_final = st.session_state.df_final
    dist_total = 0
    
    try:
        # Centraliza o mapa
        m = folium.Map(location=[df_final["Latitude"].mean(), df_final["Longitude"].mean()], zoom_start=13)
        
        # Loop para desenhar
        for i in range(len(df_final)):
            atual = df_final.iloc[i]
            
            # Marcadores
            icon_name = "home" if i == 0 else "info-sign"
            color_name = "green" if i == 0 else "red"
            folium.Marker(
                [atual['Latitude'], atual['Longitude']], 
                popup=f"{i}. {atual['Rua']}", 
                icon=folium.Icon(color=color_name, icon=icon_name)
            ).add_to(m)
            
            # Desenhar linha até o próximo
            if i < len(df_final) - 1:
                prox = df_final.iloc[i+1]
                
                # AQUI É O SEGREDO: Pausa para o servidor não bloquear e desenhar a curva
                time.sleep(0.4) 
                
                trajeto = pegar_trajeto_desenho(atual['Latitude'], atual['Longitude'], prox['Latitude'], prox['Longitude'])
                dist = obter_distancia_rodagem(atual['Latitude'], atual['Longitude'], prox['Latitude'], prox['Longitude'])
                
                folium.PolyLine(trajeto, color="blue", weight=5, opacity=0.8).add_to(m)
                dist_total += dist
                
            elif i == len(df_final) - 1:
                # Volta pra base
                base = df_final.iloc[0]
                time.sleep(0.4)
                trajeto = pegar_trajeto_desenho(atual['Latitude'], atual['Longitude'], base['Latitude'], base['Longitude'])
                dist = obter_distancia_rodagem(atual['Latitude'], atual['Longitude'], base['Latitude'], base['Longitude'])
                folium.PolyLine(trajeto, color="gray", weight=3, dash_array='5').add_to(m)
                dist_total += dist

        st_folium(m, width=900, height=500)
        
        st.success(f"✅ Rota Otimizada! Distância Total Estimada: {dist_total/1000:.1f} km")
        
        st.markdown(f'''
            <a href="{st.session_state.link_maps}" target="_blank" style="text-decoration: none;">
                <button style="
                    background-color: #4CAF50; 
                    color: white; 
                    padding: 15px 32px; 
                    text-align: center; 
                    display: inline-block; 
                    font-size: 16px; 
                    margin: 4px 2px; 
                    cursor: pointer; 
                    border-radius: 8px; 
                    border: none; 
                    width: 100%;">
                    🌍 <b>ABRIR NO GOOGLE MAPS (APP)</b>
                </button>
            </a>
            ''', unsafe_allow_html=True)
            
    except Exception as e:
        st.error(f"Erro ao desenhar mapa: {e}")
