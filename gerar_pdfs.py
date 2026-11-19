import base64
import re
from pathlib import Path
import frontmatter
import markdown
from jinja2 import Environment, FileSystemLoader
from playwright.sync_api import sync_playwright

PASTA_ENTRADA = Path("entrada_md")
PASTA_SAIDA = Path("saida_pdf")
PASTA_TEMPLATES = Path("templates")
PASTA_ASSETS = Path("assets")

PASTA_SAIDA.mkdir(exist_ok=True)


def img_para_data_uri(nome_arquivo: str) -> str:
    caminho = PASTA_ASSETS / nome_arquivo
    if not caminho.exists():
        return ""
    mime = "image/svg+xml" if caminho.suffix.lower() == ".svg" else "image/png"
    dados = base64.b64encode(caminho.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{dados}"

def md_para_html(texto: str) -> str:
    if not texto or not texto.strip():
        return ""
    return markdown.markdown(
        texto.strip(),
        extensions=["tables", "fenced_code", "nl2br"]
    )


def extrair_secoes(corpo_md: str) -> dict:
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

    return {k: md_para_html(v) for k, v in secoes.items()}


def selecionar_assets(nivel: str, trilha: str) -> dict:
    logo_uri = img_para_data_uri("logo_start.svg")

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
    template = env.get_template("plano.html")

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
            secoes = extrair_secoes(post.content)

            nivel = (meta.get("nivel") or "EFAF").strip().upper()
            trilha = (meta.get("trilha") or "cultura_digital").strip().lower()
            tempos = meta.get("tempos") or {}
            assets = selecionar_assets(nivel, trilha)

            html_renderizado = template.render(
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
            print(f"Gerado: {caminho_pdf.name} [{nivel} | {trilha}]")

        browser.close()


if __name__ == "__main__":
    processar_todos()
