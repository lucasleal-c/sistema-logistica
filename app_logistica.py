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

# --- CONFIGURAÇÃO INICIAL ---
st.set_page_config(page_title="Sistema de Rotas Inteligente", layout="wide", page_icon="🚚")

# Hack para tela não piscar/escurecer
st.markdown("""
<style>
    .stApp {opacity: 1 !important; transition: none !important;}
    header {visibility: hidden;}
    .main .block-container {padding-top: 2rem;}
</style>
""", unsafe_allow_html=True)

# --- MEMÓRIA ---
if 'rota_calculada' not in st.session_state: st.session_state.rota_calculada = False
if 'df_final' not in st.session_state: st.session_state.df_final = None
if 'link_maps' not in st.session_state: st.session_state.link_maps = ""

# --- SEGURANÇA ---
if "CHAVE_GOOGLE" in st.secrets:
    CHAVE_GOOGLE = st.secrets["CHAVE_GOOGLE"]
else:
    CHAVE_GOOGLE = "" 

# --- FUNÇÕES ---
def extrair_endereco_pdf(arquivo):
    """Lê PDF e busca o primeiro endereço encontrado"""
    try:
        with pdfplumber.open(arquivo) as pdf:
            texto = ""
            for page in pdf.pages:
                texto += page.extract_text() + "\n"
        
        texto_limpo = texto.replace("\n", " ")
        
        # Regex para endereços
        padroes = [
            r"(Rua|Av\.|Avenida|Travessa|Alameda|Estrada|Rodovia|Praça)\s+[^,]+,\s*\d+", 
            r"Endereço:\s*([^,]+,\s*\d+)",
            r"Entrega:\s*([^,]+,\s*\d+)"
        ]
        
        for padrao in padroes:
            match = re.search(padrao, texto_limpo, re.IGNORECASE)
            if match:
                return match.group(0).replace("Endereço:", "").replace("Entrega:", "").strip()
    except Exception as e:
        return None
    return None

def obter_lat_long_google(endereco):
    try:
        geolocator = GoogleV3(api_key=CHAVE_GOOGLE)
        if "Brasil" not in endereco: endereco += ", Brasil"
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

# --- BARRA LATERAL (VOLTOU!) ---
st.sidebar.header("⚙️ Configurações da Frota")
modo_rota = st.sidebar.radio("Estratégia de Roteirização:", ("Modo Econômico", "Modo Caminhoneiro"))
st.sidebar.markdown("---")
consumo = st.sidebar.number_input("Consumo (km/L):", value=8.0, step=0.5)
preco_combustivel = st.sidebar.number_input("Preço Diesel (R$):", value=5.89, step=0.10)
st.sidebar.markdown("---")
st.sidebar.info("Modo Econômico: Sempre vai para o vizinho mais próximo.\n\nModo Caminhoneiro: Tenta pegar a entrega mais distante primeiro e vir voltando.")


# --- INTERFACE PRINCIPAL ---
st.title("🚚 Sistema de Logística Inteligente")
st.markdown("---")

if not st.session_state.rota_calculada:
    st.info("📂 Arraste seus arquivos abaixo (PDFs de Notas ou Excel com endereços).")
    
    arquivos_carregados = st.file_uploader(
        "Upload de Arquivos", 
        type=["pdf", "xlsx"], 
        accept_multiple_files=True
    )

    if st.button("🚀 CALCULAR ROTA E CUSTOS"):
        if not arquivos_carregados:
            st.error("⚠️ Envie pelo menos um arquivo!")
            st.stop()
        if CHAVE_GOOGLE == "":
            st.error("⚠️ Erro: Chave do Google ausente nos Secrets!")
            st.stop()

        bar = st.progress(0)
        status = st.empty()
        enderecos_encontrados = []

        # 1. PROCESSAMENTO DE ARQUIVOS
        total_arquivos = len(arquivos_carregados)
        
        for idx, arquivo in enumerate(arquivos_carregados):
            status.text(f"Lendo: {arquivo.name}...")
            
            # EXCEL
            if arquivo.name.endswith(".xlsx"):
                try:
                    df_temp = pd.read_excel(arquivo)
                    col_endereco = None
                    possiveis = ['endereço', 'endereco', 'rua', 'logradouro', 'address', 'destino']
                    for col in df_temp.columns:
                        if col.lower() in possiveis:
                            col_endereco = col
                            break
                    if col_endereco:
                        for i, row in df_temp.iterrows():
                            end = row[col_endereco]
                            if pd.notna(end) and str(end).strip() != "":
                                enderecos_encontrados.append({'Rua': str(end), 'Origem': 'Excel'})
                except: pass

            # PDF
            elif arquivo.name.endswith(".pdf"):
                end_extraido = extrair_endereco_pdf(arquivo)
                if end_extraido:
                    enderecos_encontrados.append({'Rua': end_extraido, 'Origem': arquivo.name})

            bar.progress((idx + 1) / total_arquivos * 0.2)

        if not enderecos_encontrados:
            st.error("❌ Nenhum endereço encontrado.")
            st.stop()
            
        df = pd.DataFrame(enderecos_encontrados)
        df["Latitude"] = None
        df["Longitude"] = None

        # 2. GEOCODIFICAÇÃO
        status.text("🌍 Localizando endereços no mapa...")
        total_ends = len(df)
        
        for i, row in df.iterrows():
            lat, lon, end_oficial = obter_lat_long_google(row["Rua"])
            if lat:
                df.at[i, "Latitude"] = lat
                df.at[i, "Longitude"] = lon
                df.at[i, "Rua"] = end_oficial
            time.sleep(0.1) # Evita bloqueio
            bar.progress(0.2 + ((i + 1) / total_ends * 0.3))
        
        df = df.dropna(subset=['Latitude'])
        
        if df.empty:
            st.error("❌ Google não encontrou os endereços.")
            st.stop()

        # 3. OTIMIZAÇÃO (COM LÓGICA DE ESTRATÉGIA)
        status.text(f"🚚 Otimizando rota: {modo_rota}...")
        
        base = df.iloc[0]
        clientes = df.iloc[1:].copy()
        rota_ordenada = [base]
        posicao_atual = base
        
        # >>> LÓGICA CAMINHONEIRO (Vai longe primeiro) <<<
        if modo_rota == "Modo Caminhoneiro" and not clientes.empty:
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

        # Continua com Vizinho Mais Próximo
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
            if qtd_inicial > 0:
                bar.progress(0.5 + (passos / qtd_inicial * 0.4))
            time.sleep(0.1)

        # 4. FINALIZAR
        df_final = pd.DataFrame(rota_ordenada)
        st.session_state.df_final = df_final
        
        # Link Maps
        base_url = "https://www.google.com/maps/dir/"
        coords = [f"{r['Latitude']},{r['Longitude']}" for _, r in df_final.iterrows()]
        if len(df_final) > 1:
            coords.append(f"{df_final.iloc[0]['Latitude']},{df_final.iloc[0]['Longitude']}")
            
        st.session_state.link_maps = base_url + "/".join(coords)
        st.session_state.rota_calculada = True
        
        bar.progress(1.0)
        time.sleep(0.5)
        st.rerun()

# --- TELA DE RESULTADOS ---
if st.session_state.rota_calculada and st.session_state.df_final is not None:
    col1, col2 = st.columns([3, 1])
    with col1: st.subheader("🗺️ Resultado da Rota")
    with col2:
        if st.button("🔄 Nova Rota"):
            st.session_state.rota_calculada = False
            st.session_state.df_final = None
            st.rerun()

    df_final = st.session_state.df_final
    dist_total_metros = 0
    
    try:
        m = folium.Map(location=[df_final["Latitude"].mean(), df_final["Longitude"].mean()], zoom_start=13)
        
        for i in range(len(df_final)):
            atual = df_final.iloc[i]
            icon_cor = "green" if i == 0 else "red"
            icon_tipo = "home" if i == 0 else "flag"
            
            folium.Marker(
                [atual['Latitude'], atual['Longitude']], 
                popup=f"{i+1}. {atual['Rua']}",
                icon=folium.Icon(color=icon_cor, icon=icon_tipo)
            ).add_to(m)
            
            if i < len(df_final) - 1:
                prox = df_final.iloc[i+1]
                time.sleep(0.2) 
                trajeto = pegar_trajeto_desenho(atual['Latitude'], atual['Longitude'], prox['Latitude'], prox['Longitude'])
                dist = obter_distancia_rodagem(atual['Latitude'], atual['Longitude'], prox['Latitude'], prox['Longitude'])
                folium.PolyLine(trajeto, color="blue", weight=5, opacity=0.7).add_to(m)
                dist_total_metros += dist
            
            elif i == len(df_final) - 1 and len(df_final) > 1:
                base = df_final.iloc[0]
                time.sleep(0.2)
                trajeto = pegar_trajeto_desenho(atual['Latitude'], atual['Longitude'], base['Latitude'], base['Longitude'])
                dist = obter_distancia_rodagem(atual['Latitude'], atual['Longitude'], base['Latitude'], base['Longitude'])
                folium.PolyLine(trajeto, color="gray", weight=3, dash_array='5').add_to(m)
                dist_total_metros += dist

        # Exibir Mapa
        st_folium(m, width=900, height=500)
        
        # CÁLCULOS FINAIS
        dist_km = dist_total_metros / 1000
        litros_gastos = dist_km / consumo
        custo_total = litros_gastos * preco_combustivel

        # MÉTRICAS
        k1, k2, k3 = st.columns(3)
        k1.metric("Distância Total", f"{dist_km:.1f} km")
        k2.metric("Consumo Estimado", f"{litros_gastos:.1f} L")
        k3.metric("Custo Combustível", f"R$ {custo_total:.2f}")

        # BOTÃO MAPS
        st.markdown(f'''
            <a href="{st.session_state.link_maps}" target="_blank" style="text-decoration: none;">
                <button style="background-color: #4CAF50; color: white; padding: 15px 32px; border-radius: 8px; border: none; width: 100%; cursor: pointer; font-size: 16px; font-weight: bold; margin-top: 20px;">
                    📲 ABRIR NO GOOGLE MAPS
                </button>
            </a>
            ''', unsafe_allow_html=True)
            
    except Exception as e:
        st.error(f"Erro ao exibir mapa: {e}")
