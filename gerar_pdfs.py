import base64
import re
from io import BytesIO
from pathlib import Path
from urllib.parse import unquote

import frontmatter
import markdown
from jinja2 import Environment, FileSystemLoader
from PIL import Image, ImageFilter, ImageOps
from playwright.sync_api import sync_playwright

PASTA_ENTRADA = Path("entrada_md")
PASTA_SAIDA = Path("saida_pdf")
PASTA_TEMPLATES = Path("templates")
PASTA_ASSETS = Path("assets")

PASTA_SAIDA.mkdir(exist_ok=True)


def caminho_para_data_uri(caminho: Path) -> str:
    """Converte assets fixos do template sem alterar dimensões ou filtros."""
    if not caminho.exists():
        return ""
    ext = caminho.suffix.lower()
    mimes = {
        ".svg": "image/svg+xml",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }
    mime = mimes.get(ext, "image/png")
    dados = base64.b64encode(caminho.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{dados}"


def img_para_data_uri(nome_arquivo: str) -> str:
    return caminho_para_data_uri(PASTA_ASSETS / nome_arquivo)


def processar_imagem_conteudo(
    caminho: Path,
    largura_max: int = 1400,
    largura_min_alerta: int = 1000,
    aplicar_nitidez: bool = True,
) -> str:
    """Valida resolução, padroniza tamanho e otimiza imagens inseridas nos .md."""
    if not caminho.exists():
        return ""

    # SVGs são vetoriais: não passam pelo Pillow
    if caminho.suffix.lower() == ".svg":
        return caminho_para_data_uri(caminho)

    with Image.open(caminho) as img:
        img = ImageOps.exif_transpose(img)
        largura, altura = img.size

        if largura < largura_min_alerta:
            print(
                f"  ⚠️  Baixa resolução ({largura}x{altura}px, mínimo recomendado {largura_min_alerta}px): {caminho.name}"
            )

        if largura > largura_max:
            proporcao = largura_max / largura
            nova_altura = max(1, int(altura * proporcao))
            img = img.resize((largura_max, nova_altura), Image.Resampling.LANCZOS)

        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA")

        if aplicar_nitidez:
            img = img.filter(
                ImageFilter.UnsharpMask(radius=1.0, percent=110, threshold=3)
            )

        buffer = BytesIO()
        img.save(buffer, format="PNG", optimize=True)
        dados = base64.b64encode(buffer.getvalue()).decode("ascii")
        return f"data:image/png;base64,{dados}"


def embutir_imagens_locais(texto_md: str, pasta_md: Path) -> str:
    """Localiza imagens em Markdown, valida/otimiza com Pillow e converte para Data URI."""
    padrao_img = re.compile(r"!\[(.*?)\]\((?!https?://|data:)(.*?)\)")
    linhas_processadas = []

    for linha in texto_md.splitlines():
        em_tabela = linha.lstrip().startswith("|")
        largura_min = 500 if em_tabela else 1000

        def substituir(match):
            alt = match.group(1)
            caminho_bruto = unquote(match.group(2).strip().strip("<>"))
            caminho_limpo = unquote(match.group(2).strip().strip("<>")).replace("\\", "/")
            arq_img = (pasta_md / caminho_limpo).resolve()
            if not arq_img.exists():
                arq_img = Path(caminho_limpo).resolve()

            if not arq_img.exists():
                print(f"  ❌ Imagem não encontrada: {caminho_bruto} (esperada em {arq_img})")
                return match.group(0)

            data_uri = processar_imagem_conteudo(
                arq_img,
                largura_max=1400,
                largura_min_alerta=largura_min,
            )
            return f"![{alt}]({data_uri})"

        linhas_processadas.append(padrao_img.sub(substituir, linha))

    return "\n".join(linhas_processadas)


def md_para_html(texto: str) -> str:
    if not texto or not texto.strip():
        return ""
    return markdown.markdown(
        texto.strip(),
        extensions=["tables", "fenced_code", "nl2br"],
    )


def extrair_secoes(corpo_md: str, pasta_md: Path) -> dict:
    secoes = {}
    chave_atual = None
    buffer = []

    mapa_chaves = {
        "habilidade": "habilidade",
        "habilidades": "habilidade",
        "conteúdos": "conteudos",
        "objetivos de aprendizagem": "objetivos",
        "o que os estudantes aprenderão?": "o_que_aprenderao",
        "recursos e materiais": "recursos",
        "1. ponto de partida": "ponto_partida",
        "reflexão inicial": "reflexao_inicial",
        "2. prática guiada": "pratica_guiada_intro",
        "proposta": "proposta",
        "desenvolvimento": "desenvolvimento",
        "para ampliar a mediação": "ampliar_mediacao",
        "fique atento": "fique_atento",
        "4. fechamento": "fechamento_intro",
        "resultado esperado": "resultado_esperado",
        "teste": "teste",
        "verificação da aprendizagem": "verificacao_aprendizagem",
        "como verificar se os estudantes alcançaram os objetivos da aula?": "verificacao_aprendizagem",
        "como verificar se os estudantes alcançaram o objetivo da aula?": "verificacao_aprendizagem",
    }

    for linha in corpo_md.splitlines():
        match = re.match(r"^(#{2,3})\s+(.+?)\s*$", linha)
        if match:
            titulo_limpo = match.group(2).strip().lower()
            if titulo_limpo in mapa_chaves:
                if chave_atual:
                    secoes[chave_atual] = "\n".join(buffer).strip()
                chave_atual = mapa_chaves[titulo_limpo]
                buffer = []
                continue
        if chave_atual:
            buffer.append(linha)

    if chave_atual:
        secoes[chave_atual] = "\n".join(buffer).strip()

    return {
        k: md_para_html(embutir_imagens_locais(v, pasta_md))
        for k, v in secoes.items()
    }


def extrair_secoes_efai(corpo_md: str, pasta_md: Path) -> dict:
    """Separa apenas pelos cabeçalhos ## para preservar ### e #### dentro de Momento plataforma."""
    mapa_chaves = {
        "habilidade bncc": "habilidade",
        "habilidade": "habilidade",
        "habilidades": "habilidade",
        "objetivo de aprendizagem": "objetivo",
        "objetivos de aprendizagem": "objetivo",
        "recursos e materiais": "recursos",
        "aquecimento": "aquecimento",
        "momento plataforma": "momento_plataforma",
        "arremate": "arremate",
        "mão na massa": "mao_na_massa",
        "atividades extras": "atividades_extras",
        "diário de bordo": "diario_de_bordo",
    }

    secoes_raw = {}
    chave_atual = None

    for linha in corpo_md.splitlines():
        match = re.match(r"^##\s+(.+?)\s*$", linha)
        if match:
            titulo_limpo = match.group(1).strip().lower()
            chave_atual = mapa_chaves.get(titulo_limpo)
            if chave_atual:
                secoes_raw[chave_atual] = []
            continue
        if chave_atual:
            secoes_raw[chave_atual].append(linha)

    secoes_html = {}
    for chave, linhas in secoes_raw.items():
        texto_md = "\n".join(linhas).strip()
        if not texto_md:
            continue
        texto_md = embutir_imagens_locais(texto_md, pasta_md)
        secoes_html[chave] = md_para_html(texto_md)

    return secoes_html


def selecionar_assets(nivel: str, trilha: str) -> dict:
    logo_uri = img_para_data_uri("logo_start.svg")

    if nivel == "EFAI":
        return {
            "topo_efai": img_para_data_uri("cabeçalho efai.svg"),
            "rodape_efai_esq": img_para_data_uri("icones efai.svg"),
            "logo_efai_dir": logo_uri,
        }

    if nivel == "EFAF":
        cabecalho_uri = img_para_data_uri("cabecalho_efaf.png")
        if trilha == "programacao":
            rodape_esq_uri = img_para_data_uri("icones_efaf_programacao.png")
        else:
            rodape_esq_uri = img_para_data_uri("icones_efaf_cultura_digital.png")
        altura_rodape_esq = "58px"
    else:
        rodape_esq_uri = img_para_data_uri("figuras_em.png")
        altura_rodape_esq = "26px"
        if trilha == "programacao":
            cabecalho_uri = img_para_data_uri("cabecalho_em_programacao.png")
        else:
            cabecalho_uri = img_para_data_uri("cabecalho_em_cultura_digital.png")

    return {
        "logo_uri": logo_uri,
        "cabecalho_uri": cabecalho_uri,
        "rodape_esq_uri": rodape_esq_uri,
        "altura_rodape_esq": altura_rodape_esq,
    }


def processar_todos():
    env = Environment(loader=FileSystemLoader(str(PASTA_TEMPLATES)))
    template_padrao = env.get_template("plano.html")
    template_efai = env.get_template("plano_efai.html")

    arquivos_md = sorted(PASTA_ENTRADA.rglob("*.md"))
    if not arquivos_md:
        print(f"Nenhum arquivo .md encontrado em {PASTA_ENTRADA.resolve()}")
        return

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()

        for arquivo in arquivos_md:
            post = frontmatter.load(arquivo)
            meta = post.metadata

            nivel = (meta.get("nivel") or "EFAF").strip().upper()
            trilha = (meta.get("trilha") or "cultura_digital").strip().lower()
            tempos = meta.get("tempos") or {}
            assets = selecionar_assets(nivel, trilha)

            if nivel == "EFAI":
                secoes = extrair_secoes_efai(post.content, arquivo.parent)
                html_renderizado = template_efai.render(
                    meta=meta,
                    tempos=tempos,
                    s=secoes,
                    assets=assets,
                )
            else:
                secoes = extrair_secoes(post.content, arquivo.parent)
                html_renderizado = template_padrao.render(
                    meta=meta,
                    nivel=nivel,
                    trilha=trilha,
                    tempos=tempos,
                    s=secoes,
                    assets=assets,
                )

            page.set_content(html_renderizado, wait_until="networkidle")
            page.evaluate("() => document.fonts.ready")

            # Auto-balanceamento: evita página final "órfã" com poucas linhas
            page.evaluate("""
                () => {
                    const alturaA4Px = 1122;
                    const alturaTotal = document.body.scrollHeight;
                    const resto = alturaTotal % alturaA4Px;
                    if (alturaTotal > alturaA4Px && resto > 0 && resto < (alturaA4Px * 0.20)) {
                        document.body.classList.add('modo-compacto');
                    }
                }
            """)

            caminho_relativo = arquivo.relative_to(PASTA_ENTRADA)
            caminho_pdf = (PASTA_SAIDA / caminho_relativo).with_suffix(".pdf")
            caminho_pdf.parent.mkdir(parents=True, exist_ok=True)

            page.pdf(
                path=str(caminho_pdf),
                format="A4",
                print_background=True,
                display_header_footer=False,
                margin={
                    "top": "0mm",
                    "bottom": "0mm",
                    "left": "0mm",
                    "right": "0mm",
                },
            )

            info_tipo = "EFAI" if nivel == "EFAI" else f"{nivel} | {trilha}"
            print(f"Gerado: {caminho_pdf.relative_to(PASTA_SAIDA)} [{info_tipo}]")

        browser.close()


if __name__ == "__main__":
    processar_todos()