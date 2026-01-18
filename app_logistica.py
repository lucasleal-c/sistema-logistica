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

# --- 1. CONFIGURAÇÃO ---
st.set_page_config(page_title="Roteirizador Estável", layout="wide", page_icon="🚚")

st.markdown("""
<style>
    .stApp {opacity: 1 !important; transition: none !important;}
    header {visibility: hidden;}
    .main .block-container {padding-top: 2rem;}
</style>
""", unsafe_allow_html=True)

# --- 2. MEMÓRIA ---
if 'rota_calculada' not in st.session_state: st.session_state.rota_calculada = False
if 'df_final' not in st.session_state: st.session_state.df_final = None
if 'link_unico' not in st.session_state: st.session_state.link_unico = ""

# --- 3. API KEY ---
if "CHAVE_GOOGLE" in st.secrets:
    CHAVE_GOOGLE = st.secrets["CHAVE_GOOGLE"]
else:
    CHAVE_GOOGLE = "" 

# --- 4. FUNÇÕES ---

def extrair_endereco_pdf(arquivo):
    try:
        with pdfplumber.open(arquivo) as pdf:
            texto = ""
            for page in pdf.pages:
                texto += page.extract_text() + "\n"
        
        texto_limpo = texto.replace("\n", " ")
        padroes = [
            r"(Rua|Av\.|Avenida|Travessa|Alameda|Estrada|Rodovia|Praça|Via|Rod\.)\s+[^,]+,\s*\d+[^,]*,\s*[^,]+-[A-Z]{2}",
            r"(Rua|Av\.|Avenida|Travessa|Alameda|Estrada|Rodovia|Praça|Via|Rod\.)\s+[^,]+,\s*\d+",
            r"Endereço:\s*([^,]+,\s*\d+)",
            r"Entrega:\s*([^,]+,\s*\d+)"
        ]
        for padrao in padroes:
            match = re.search(padrao, texto_limpo, re.IGNORECASE)
            if match:
                return match.group(0).replace("Endereço:", "").replace("Entrega:", "").strip()
    except: return None
    return None

def obter_lat_long_google(endereco):
    try:
        geolocator = GoogleV3(api_key=CHAVE_GOOGLE)
        busca = endereco if "Brasil" in endereco else f"{endereco}, Brasil"
        local = geolocator.geocode(busca)
        
        if local:
            if abs(local.latitude - (-14.235)) < 1.0 and abs(local.longitude - (-51.925)) < 1.0:
                return None, None, "Erro: Centro Geográfico (Genérico)"
            if local.latitude > -18.0: 
                 return None, None, f"Erro: Fora de SP (Lat: {local.latitude:.2f})"
            return local.latitude, local.longitude, local.address
    except Exception as e: return None, None, f"Erro API: {str(e)}"
    return None, None, "Não encontrado"

# --- AQUI ESTA A CORREÇÃO (RETRY + SLEEP) ---
def obter_distancia_rodagem(lat1, lon1, lat2, lon2):
    """Tenta pegar a distancia real 3 vezes antes de desistir"""
    loc = f"{lon1},{lat1};{lon2},{lat2}"
    url = f"http://router.project-osrm.org/route/v1/driving/{loc}?overview=false"
    headers = {"User-Agent": "SistemaLogistica/2.0"}
    
    for tentativa in range(3): # Tenta 3 vezes
        try:
            r = requests.get(url, headers=headers, timeout=5) # Aumentei timeout
            if r.status_code == 200:
                return r.json()['routes'][0]['distance']
            elif r.status_code == 429: # Erro "Too Many Requests"
                time.sleep(1.5) # Espera 1.5s e tenta de novo
                continue
        except:
            time.sleep(1)
            continue
            
    # Se falhar 3x, usa linha reta
    return geodesic((lat1, lon1), (lat2, lon2)).meters

def pegar_trajeto_desenho(lat1, lon1, lat2, lon2):
    """Tenta pegar o desenho da estrada 3 vezes"""
    loc = f"{lon1},{lat1};{lon2},{lat2}"
    url = f"http://router.project-osrm.org/route/v1/driving/{loc}?overview=full&geometries=polyline"
    headers = {"User-Agent": "SistemaLogistica/2.0"}
    
    for tentativa in range(3):
        try:
            r = requests.get(url, headers=headers, timeout=5)
            if r.status_code == 200:
                return polyline.decode(r.json()['routes'][0]['geometry']), "estrada"
            elif r.status_code == 429:
                time.sleep(1.5)
                continue
        except:
            time.sleep(1)
            
    # Se falhar, retorna linha reta e avisa que é falha
    return [(lat1, lon1), (lat2, lon2)], "reta"

# --- 5. INTERFACE ---
st.sidebar.header("⚙️ Configurações")
modo_rota = st.sidebar.radio("Estratégia:", ("Modo Econômico", "Modo Caminhoneiro"))
st.sidebar.info("Agora o sistema tenta reconectar se o mapa falhar, evitando linhas retas.")
st.sidebar.markdown("---")
consumo = st.sidebar.number_input("Consumo (km/L):", value=3.5)
preco = st.sidebar.number_input("Diesel (R$):", value=5.89)

st.title("🚚 Roteirizador Estável (Link Único)")
st.markdown("---")

if not st.session_state.rota_calculada:
    arquivos = st.file_uploader("Upload Arquivos", type=["pdf", "xlsx"], accept_multiple_files=True)

    if st.button("🚀 CALCULAR ROTA"):
        if not arquivos: st.stop()
        
        bar = st.progress(0)
        status = st.empty()
        ends_encontrados = []

        # LEITURA
        for idx, arq in enumerate(arquivos):
            status.text(f"Lendo: {arq.name}")
            if arq.name.endswith(".xlsx"):
                try:
                    df_t = pd.read_excel(arq)
                    cols = [c.lower() for c in df_t.columns]
                    df_t.columns = cols
                    col_rua = next((c for c in cols if c in ['rua', 'logradouro', 'endereço', 'endereco']), None)
                    col_num = next((c for c in cols if c in ['número', 'numero', 'num']), None)
                    col_cidade = next((c for c in cols if c in ['cidade', 'município', 'municipio']), None)
                    
                    if col_rua:
                        for _, row in df_t.iterrows():
                            rua = str(row[col_rua]) if pd.notna(row[col_rua]) else ""
                            num = str(row[col_num]) if col_num and pd.notna(row[col_num]) else ""
                            cidade = str(row[col_cidade]) if col_cidade and pd.notna(row[col_cidade]) else ""
                            end_full = f"{rua}, {num} - {cidade}" if cidade else f"{rua}, {num}"
                            if len(rua) > 3: ends_encontrados.append({'Rua': end_full, 'Origem': 'Excel'})
                except: pass
            elif arq.name.endswith(".pdf"):
                end = extrair_endereco_pdf(arq)
                if end: ends_encontrados.append({'Rua': end, 'Origem': arq.name})
            bar.progress((idx+1)/len(arquivos)*0.2)

        # GEOCODIFICAÇÃO
        df = pd.DataFrame(ends_encontrados)
        if df.empty: st.error("Nenhum endereço achado."); st.stop()
        df["Latitude"] = None; df["Longitude"] = None; df["Status"] = "Pendente"
        
        status.text("🌍 Validando coordenadas...")
        for i, row in df.iterrows():
            lat, lon, full = obter_lat_long_google(row["Rua"])
            if lat:
                df.at[i, "Latitude"] = lat; df.at[i, "Longitude"] = lon; df.at[i, "Rua"] = full; df.at[i, "Status"] = "OK"
            else: df.at[i, "Status"] = full
            time.sleep(0.5) # Delay aumentado para evitar bloqueio
            bar.progress(0.2 + ((i+1)/len(df)*0.3))

        # FILTROS
        df = df[df["Status"] == "OK"].copy()
        df["Latitude"] = pd.to_numeric(df["Latitude"])
        df["Longitude"] = pd.to_numeric(df["Longitude"])
        if len(df) < 2: st.error("Menos de 2 endereços válidos."); st.stop()

        # OTIMIZAÇÃO
        status.text(f"🚚 Otimizando (Pode demorar um pouco para garantir precisão)...")
        base = df.iloc[0]; clientes = df.iloc[1:].copy(); rota = [base]; atual = base
        
        # Modo Caminhoneiro
        if modo_rota == "Modo Caminhoneiro" and not clientes.empty:
            max_d = -1; idx_max = -1
            for i, c in clientes.iterrows():
                try:
                    d = obter_distancia_rodagem(base['Latitude'], base['Longitude'], c['Latitude'], c['Longitude'])
                    if d > max_d: max_d = d; idx_max = i
                except: continue
                time.sleep(0.1) # Respira entre calculos
            if idx_max != -1:
                longe = clientes.loc[idx_max]; rota.append(longe); atual = longe; clientes = clientes.drop(idx_max)

        # Vizinho Próximo
        passos = 0; total = len(clientes)
        while not clientes.empty:
            min_d = float('inf'); idx_min = -1
            for i, c in clientes.iterrows():
                try:
                    d = obter_distancia_rodagem(atual['Latitude'], atual['Longitude'], c['Latitude'], c['Longitude'])
                    if d < min_d: min_d = d; idx_min = i
                except: continue
            if idx_min != -1: prox = clientes.loc[idx_min]; rota.append(prox); atual = prox; clientes = clientes.drop(idx_min)
            else: r = clientes.iloc[0]; rota.append(r); clientes = clientes.drop(r.name)
            passos+=1; bar.progress(0.5 + (passos/total*0.4))
            time.sleep(0.2) # Delay de segurança
        
        # GERA LINK ÚNICO
        df_final = pd.DataFrame(rota)
        st.session_state.df_final = df_final
        
        base_url = "https://www.google.com/maps/dir/"
        coords = [f"{r['Latitude']},{r['Longitude']}" for _, r in df_final.iterrows()]
        coords.append(f"{df_final.iloc[0]['Latitude']},{df_final.iloc[0]['Longitude']}")
        
        st.session_state.link_unico = base_url + "/".join(coords)
        st.session_state.rota_calculada = True
        st.rerun()

# --- RESULTADO ---
if st.session_state.rota_calculada and st.session_state.df_final is not None:
    col1, col2 = st.columns([3,1])
    qtd_entregas = len(st.session_state.df_final)-1
    with col1: st.subheader(f"🗺️ Rota Otimizada ({qtd_entregas} entregas)")
    with col2: 
        if st.button("🔄 Reiniciar"): 
            st.session_state.rota_calculada = False; st.rerun()
    
    df_f = st.session_state.df_final
    
    m = folium.Map(location=[df_f["Latitude"].mean(), df_f["Longitude"].mean()], zoom_start=10)
    dist_total = 0
    
    status_msg = st.empty()
    status_msg.info("Desenhando mapa... aguarde.")
    
    for i in range(len(df_f)):
        pt = df_f.iloc[i]
        icon = "home" if i==0 else "info-sign"
        color = "green" if i==0 else "blue"
        if i == len(df_f)-1: color = "red"; icon = "flag"
        
        folium.Marker([pt['Latitude'], pt['Longitude']], popup=f"{i}. {pt['Rua']}", icon=folium.Icon(color=color, icon=icon)).add_to(m)
        
        if i < len(df_f)-1:
            dest = df_f.iloc[i+1]
            traj, tipo = pegar_trajeto_desenho(pt['Latitude'], pt['Longitude'], dest['Latitude'], dest['Longitude'])
            dist = obter_distancia_rodagem(pt['Latitude'], pt['Longitude'], dest['Latitude'], dest['Longitude'])
            
            # Se for linha reta (erro), desenha vermelho pontilhado
            cor_linha = "blue" if tipo == "estrada" else "red"
            estilo_linha = None if tipo == "estrada" else "5, 10"
            opacidade = 0.7 if tipo == "estrada" else 0.4
            
            folium.PolyLine(traj, color=cor_linha, weight=4, dash_array=estilo_linha, opacity=opacidade).add_to(m)
            dist_total += dist
            time.sleep(0.2) # Delay para desenhar sem travar
        else:
            base = df_f.iloc[0]
            traj, tipo = pegar_trajeto_desenho(pt['Latitude'], pt['Longitude'], base['Latitude'], base['Longitude'])
            dist = obter_distancia_rodagem(pt['Latitude'], pt['Longitude'], base['Latitude'], base['Longitude'])
            folium.PolyLine(traj, color="gray", weight=2, dash_array='5').add_to(m)
            dist_total += dist

    status_msg.empty()
    st_folium(m, width=900, height=400)
    
    km = dist_total/1000
    custo = (km/consumo)*preco
    c1,c2,c3 = st.columns(3)
    c1.metric("Distância Calc.", f"{km:.1f} km")
    c2.metric("Diesel", f"{(km/consumo):.1f} L")
    c3.metric("Custo", f"R$ {custo:.2f}")
    
    if qtd_entregas > 9:
        st.warning(f"⚠️ **Atenção:** Rota com {qtd_entregas} paradas. Verifique se o Google Maps do celular carregou todas.")
    
    st.markdown(f'''
        <a href="{st.session_state.link_unico}" target="_blank">
            <button style="background-color:#E91E63;color:white;padding:20px;width:100%;border:none;border-radius:10px;font-size:18px;font-weight:bold;cursor:pointer;">
                📲 ABRIR NO GOOGLE MAPS
            </button>
        </a>
    ''', unsafe_allow_html=True)

    with st.expander("📄 Detalhes"):
        st.dataframe(df_f[["Rua", "Origem"]])
