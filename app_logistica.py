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

# --- 1. CONFIGURAÇÃO DA PÁGINA (Deve ser a primeira linha) ---
st.set_page_config(page_title="Roteirizador Noroeste SP", layout="wide", page_icon="🚚")

# CSS para evitar que a tela pisque
st.markdown("""
<style>
    .stApp {opacity: 1 !important; transition: none !important;}
    header {visibility: hidden;}
    .main .block-container {padding-top: 2rem;}
</style>
""", unsafe_allow_html=True)

# --- 2. MEMÓRIA (SESSION STATE) ---
if 'rota_calculada' not in st.session_state: st.session_state.rota_calculada = False
if 'df_final' not in st.session_state: st.session_state.df_final = None
if 'link_maps' not in st.session_state: st.session_state.link_maps = ""

# --- 3. SEGURANÇA (CHAVE GOOGLE) ---
if "CHAVE_GOOGLE" in st.secrets:
    CHAVE_GOOGLE = st.secrets["CHAVE_GOOGLE"]
else:
    CHAVE_GOOGLE = "" 

# --- 4. FUNÇÕES ---

def extrair_endereco_pdf(arquivo):
    """Lê PDF e tenta achar endereço usando Regex"""
    try:
        with pdfplumber.open(arquivo) as pdf:
            texto = ""
            for page in pdf.pages:
                texto += page.extract_text() + "\n"
        
        texto_limpo = texto.replace("\n", " ")
        
        # Padrões comuns de endereço
        padroes = [
            r"(Rua|Av\.|Avenida|Travessa|Alameda|Estrada|Rodovia|Praça|Via|Rod\.)\s+[^,]+,\s*\d+", 
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
    """Geocodificação com filtro para evitar o 'Buraco Negro' do Mato Grosso"""
    try:
        geolocator = GoogleV3(api_key=CHAVE_GOOGLE)
        # Adiciona Brasil se não tiver
        busca = endereco if "Brasil" in endereco else f"{endereco}, Brasil"
        
        local = geolocator.geocode(busca)
        
        if local:
            # FILTRO ANTI-ERRO (Centro Geográfico do Brasil)
            # O Google retorna aprox -14.23, -51.92 quando não sabe onde é
            lat_erro = -14.235
            lon_erro = -51.925
            
            diff_lat = abs(local.latitude - lat_erro)
            diff_lon = abs(local.longitude - lon_erro)

            # Se for muito perto dessas coordenadas (margem de 0.5 grau), é erro
            if diff_lat < 0.5 and diff_lon < 0.5:
                return None, None, "Endereço Genérico (Erro)"
                
            return local.latitude, local.longitude, local.address
    except:
        return None, None, "Erro API"
    return None, None, "Não encontrado"

def obter_distancia_rodagem(lat1, lon1, lat2, lon2):
    """Calcula distância. Se OSRM falhar, usa linha reta (geodesic)"""
    try:
        # Tenta OSRM (Estradas reais)
        loc = f"{lon1},{lat1};{lon2},{lat2}"
        url = f"http://router.project-osrm.org/route/v1/driving/{loc}?overview=false"
        headers = {"User-Agent": "SistemaLogistica/1.0"}
        r = requests.get(url, headers=headers, timeout=2)
        if r.status_code == 200: 
            return r.json()['routes'][0]['distance']
    except:
        pass
    
    # Fallback: Linha reta (matemática)
    try:
        return geodesic((lat1, lon1), (lat2, lon2)).meters
    except:
        return 9999999 # Retorna numero gigante se der erro matematico

def pegar_trajeto_desenho(lat1, lon1, lat2, lon2):
    """Pega geometria para desenhar no mapa"""
    loc = f"{lon1},{lat1};{lon2},{lat2}"
    url = f"http://router.project-osrm.org/route/v1/driving/{loc}?overview=full&geometries=polyline"
    headers = {"User-Agent": "SistemaLogistica/1.0"}
    try:
        r = requests.get(url, headers=headers, timeout=2)
        if r.status_code == 200:
            return polyline.decode(r.json()['routes'][0]['geometry'])
    except: pass
    return [(lat1, lon1), (lat2, lon2)]

# --- 5. BARRA LATERAL ---
st.sidebar.header("⚙️ Configurações")
modo_rota = st.sidebar.radio("Estratégia:", ("Modo Econômico", "Modo Caminhoneiro"))

st.sidebar.info(
    "**Modo Econômico:** Vai para o ponto mais próximo.\n\n"
    "**Modo Caminhoneiro:** Procura o ponto mais longe primeiro (para voltar recolhendo)."
)

st.sidebar.markdown("---")
consumo = st.sidebar.number_input("Consumo (km/L):", value=3.5, step=0.1)
preco_combustivel = st.sidebar.number_input("Preço Diesel (R$):", value=5.89, step=0.10)

# --- 6. INTERFACE PRINCIPAL ---
st.title("🚚 Roteirizador Noroeste Paulista")
st.markdown("---")

if not st.session_state.rota_calculada:
    arquivos_carregados = st.file_uploader(
        "Upload de Arquivos (PDF ou Excel)", 
        type=["pdf", "xlsx"], 
        accept_multiple_files=True
    )

    if st.button("🚀 CALCULAR ROTA"):
        if not arquivos_carregados:
            st.error("⚠️ Nenhum arquivo enviado.")
            st.stop()
        if CHAVE_GOOGLE == "":
            st.error("⚠️ Sem Chave do Google configurada.")
            st.stop()

        bar = st.progress(0)
        status = st.empty()
        enderecos_encontrados = []

        # A. LEITURA
        total_arquivos = len(arquivos_carregados)
        for idx, arquivo in enumerate(arquivos_carregados):
            status.text(f"Lendo: {arquivo.name}")
            
            # Excel
            if arquivo.name.endswith(".xlsx"):
                try:
                    df_temp = pd.read_excel(arquivo)
                    # Procura coluna de endereço flexível
                    col_end = next((c for c in df_temp.columns if c.lower() in ['endereço', 'endereco', 'rua', 'dest', 'local']), None)
                    
                    if col_end:
                        for _, row in df_temp.iterrows():
                            val = row[col_end]
                            if pd.notna(val) and str(val).strip() != "":
                                enderecos_encontrados.append({'Rua': str(val), 'Origem': 'Excel'})
                except: pass
            
            # PDF
            elif arquivo.name.endswith(".pdf"):
                end = extrair_endereco_pdf(arquivo)
                if end: enderecos_encontrados.append({'Rua': end, 'Origem': arquivo.name})

            bar.progress((idx + 1) / total_arquivos * 0.2)

        if not enderecos_encontrados:
            st.error("❌ Não achei endereços válidos.")
            st.stop()

        # Prepara DataFrame
        df = pd.DataFrame(enderecos_encontrados)
        df["Latitude"] = None
        df["Longitude"] = None
        df["Status"] = "Pendente"

        # B. GEOCODIFICAÇÃO (GOOGLE)
        status.text("🌍 Buscando coordenadas...")
        total_ends = len(df)
        
        for i, row in df.iterrows():
            lat, lon, end_full = obter_lat_long_google(row["Rua"])
            if lat:
                df.at[i, "Latitude"] = lat
                df.at[i, "Longitude"] = lon
                df.at[i, "Rua"] = end_full
                df.at[i, "Status"] = "OK"
            else:
                df.at[i, "Status"] = "Erro"
            
            time.sleep(0.1)
            bar.progress(0.2 + ((i + 1) / total_ends * 0.3))

        # C. LIMPEZA E FILTRO
        df_erros = df[df["Status"] == "Erro"]
        df = df.dropna(subset=['Latitude']).reset_index(drop=True)
        
        # Garante que são números (Correção do erro do caminhoneiro)
        df["Latitude"] = pd.to_numeric(df["Latitude"])
        df["Longitude"] = pd.to_numeric(df["Longitude"])

        if not df_erros.empty:
            st.warning(f"⚠️ Atenção: {len(df_erros)} endereços não foram localizados.")
            with st.expander("Ver endereços com erro"):
                st.dataframe(df_erros)

        if len(df) < 2:
            st.error("❌ Preciso de pelo menos 2 endereços válidos para traçar uma rota.")
            st.stop()

        # D. OTIMIZAÇÃO DE ROTA
        status.text(f"🚚 Otimizando: {modo_rota}...")
        
        # Base é o primeiro da lista
        base = df.iloc[0]
        clientes = df.iloc[1:].copy()
        rota_ordenada = [base]
        posicao_atual = base
        
        # --- LÓGICA CAMINHONEIRO (CORRIGIDA) ---
        if modo_rota == "Modo Caminhoneiro" and not clientes.empty:
            max_dist = -1
            idx_max = -1
            
            # Varre a lista procurando o mais longe da BASE
            for i, cliente in clientes.iterrows():
                try:
                    d = obter_distancia_rodagem(
                        base['Latitude'], base['Longitude'], 
                        cliente['Latitude'], cliente['Longitude']
                    )
                    if d > max_dist:
                        max_dist = d
                        idx_max = i
                except:
                    continue # Se der erro num ponto, pula pro próximo
            
            # Se achou alguém longe, ele vira o próximo destino
            if idx_max != -1:
                destino_longe = clientes.loc[idx_max]
                rota_ordenada.append(destino_longe)
                posicao_atual = destino_longe
                clientes = clientes.drop(idx_max)

        # --- LÓGICA VIZINHO MAIS PRÓXIMO (RESTO DA ROTA) ---
        passos = 0
        qtd_inicial = len(clientes)
        
        while not clientes.empty:
            min_dist = float('inf')
            idx_min = -1
            
            for i, cliente in clientes.iterrows():
                try:
                    d = obter_distancia_rodagem(
                        posicao_atual['Latitude'], posicao_atual['Longitude'], 
                        cliente['Latitude'], cliente['Longitude']
                    )
                    if d < min_dist:
                        min_dist = d
                        idx_min = i
                except: continue

            if idx_min != -1:
                prox = clientes.loc[idx_min]
                rota_ordenada.append(prox)
                posicao_atual = prox
                clientes = clientes.drop(idx_min)
            else:
                # Se sobrar alguém mas não conseguir calcular distância, força adição
                restante = clientes.iloc[0]
                rota_ordenada.append(restante)
                clientes = clientes.drop(restante.name)
            
            passos += 1
            if qtd_inicial > 0:
                bar.progress(0.5 + (passos / qtd_inicial * 0.4))
            time.sleep(0.05)

        # E. FINALIZAÇÃO
        df_final = pd.DataFrame(rota_ordenada)
        st.session_state.df_final = df_final
        
        # Cria Link Google Maps
        base_url = "https://www.google.com/maps/dir/"
        coords = [f"{r['Latitude']},{r['Longitude']}" for _, r in df_final.iterrows()]
        # Volta pra base no final
        coords.append(f"{df_final.iloc[0]['Latitude']},{df_final.iloc[0]['Longitude']}")
        
        st.session_state.link_maps = base_url + "/".join(coords)
        st.session_state.rota_calculada = True
        
        bar.progress(1.0)
        time.sleep(0.5)
        st.rerun()

# --- 7. TELA DE RESULTADOS ---
if st.session_state.rota_calculada and st.session_state.df_final is not None:
    col1, col2 = st.columns([3, 1])
    with col1: st.subheader("🗺️ Rota Otimizada")
    with col2:
        if st.button("🔄 Reiniciar"):
            st.session_state.rota_calculada = False
            st.session_state.df_final = None
            st.rerun()

    df_final = st.session_state.df_final
    dist_total_metros = 0
    
    try:
        # Centraliza o mapa
        m = folium.Map(location=[df_final["Latitude"].mean(), df_final["Longitude"].mean()], zoom_start=10)
        
        for i in range(len(df_final)):
            atual = df_final.iloc[i]
            
            # Cores dos ícones
            cor = "blue"
            icone = "info-sign"
            if i == 0: cor = "green"; icone = "home" # Início
            elif i == len(df_final) - 1: cor = "red"; icone = "flag" # Fim (antes de voltar)
            
            folium.Marker(
                [atual['Latitude'], atual['Longitude']], 
                popup=f"{i+1}. {atual['Rua']}",
                icon=folium.Icon(color=cor, icon=icone)
            ).add_to(m)
            
            # Desenha linha até o próximo
            if i < len(df_final) - 1:
                prox = df_final.iloc[i+1]
                trajeto = pegar_trajeto_desenho(atual['Latitude'], atual['Longitude'], prox['Latitude'], prox['Longitude'])
                dist = obter_distancia_rodagem(atual['Latitude'], atual['Longitude'], prox['Latitude'], prox['Longitude'])
                
                folium.PolyLine(trajeto, color="blue", weight=4, opacity=0.7).add_to(m)
                dist_total_metros += dist
            
            # Volta pra base
            elif i == len(df_final) - 1:
                base = df_final.iloc[0]
                trajeto = pegar_trajeto_desenho(atual['Latitude'], atual['Longitude'], base['Latitude'], base['Longitude'])
                dist = obter_distancia_rodagem(atual['Latitude'], atual['Longitude'], base['Latitude'], base['Longitude'])
                
                folium.PolyLine(trajeto, color="gray", weight=2, dash_array='5').add_to(m)
                dist_total_metros += dist

        st_folium(m, width=900, height=500)
        
        # Métricas Finais
        km_total = dist_total_metros / 1000
        litros = km_total / consumo
        custo = litros * preco_combustivel
        
        c1, c2, c3 = st.columns(3)
        c1.metric("Distância Total", f"{km_total:.1f} km")
        c2.metric("Diesel Estimado", f"{litros:.1f} L")
        c3.metric("Custo Total", f"R$ {custo:.2f}")
        
        st.markdown(f'''
            <a href="{st.session_state.link_maps}" target="_blank">
                <button style="background-color:#4CAF50;color:white;padding:15px;width:100%;border:none;border-radius:5px;cursor:pointer;font-weight:bold;font-size:16px;">
                    📲 ABRIR NO GOOGLE MAPS
                </button>
            </a>
        ''', unsafe_allow_html=True)

    except Exception as e:
        st.error(f"Erro ao desenhar mapa: {e}")
