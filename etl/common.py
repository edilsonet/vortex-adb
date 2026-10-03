"""Utilidades compartilhadas pelo ETL do banco ANAC.

Foco em duas coisas que quebram silenciosamente se ignoradas:

1. **Encoding e delimitador variam por arquivo.** A ANAC exporta parte da série
   em UTF-8 com BOM e parte em latin-1; alguns arquivos usam `;`, outros `,`.
   `read_delimited` detecta os dois e cai de uma codificação para a outra.
2. **Documento nem sempre é documento.** No RAB vem CNPJ limpo ou a string
   `Indisponível`; no SISANT o CPF chega mascarado (`****525.372***`). As funções
   de documento classificam a natureza da pessoa e marcam o que não é
   identificável, em vez de gravar máscara como se fosse identidade.
"""

from __future__ import annotations

import csv
import json
import re
import sys
import unicodedata
from pathlib import Path

# Windows: o console não aceita UTF-8 por padrão e Titles com acento viram lixo.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
FONTE = Path(r"C:\Users\edils\dados-anac")
BUILD = ROOT / "build"
SCHEMA = ROOT / "schema" / "schema.sql"
DB_PATH = BUILD / "anac.db"

MESES = [
    "2025-09", "2025-10", "2025-11", "2025-12", "2026-01", "2026-02",
    "2026-03", "2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09",
]

# Valores que a ANAC usa para "não temos o dado". Não são documentos.
_SEM_DOCUMENTO = {
    "", "indisponivel", "indisponível", "indisponíve", "n/a", "na", "null",
    "none", "-", "--", "sem informacao", "não informado", "nao informado",
}

_DIGITS = re.compile(r"\D+")
# Entidade HTML sem ponto e vírgula: a exportação da ANAC trunca alguns nomes
# no meio da codificação (`&#1050` sem o `;`), então o regex usual não pega.
_ENTIDADE = re.compile(r"&#(\d{2,6});?")
# Caractere de largura zero: invisível, mas quebra a comparação de nomes e
# aparece como lixo na tela. A lista é escrita como pontos de código porque
# editar invisíveis no fonte é pedir para perdê-los sem querer.
# Escrito como ponto de código, e não como caractere literal: um caractere de
# largura zero é invisível no editor, e um arquivo-fonte que depende de algo
# invisível quebra na primeira vez que alguém salva com outra codificação.
ZERO_WIDTH = ("​", "‌", "‍", "﻿")
_ZERO_WIDTH = str.maketrans({c: None for c in ZERO_WIDTH})


def desserializa_html(valor: str | None) -> str | None:
    """Desfaz as entidades HTML que a fonte traz dentro do texto.

    Não é um detalhe cosmético: 41 nomes chegaram ao banco como
    `&#8203;&#8203;ATLÂNTICA RADIADORES LTDA`, o que fazia a empresa aparecer com
    um prefixo de lixo e a busca por nome limpo não encontrá-la.

    Só converte entidades numéricas — `&amp;` e companhia saem como estão,
    porque no nome de uma empresa esses literais são o próprio nome.
    """
    if valor is None or "&" not in str(valor):
        return valor
    texto = str(valor)
    if _ENTIDADE.search(texto):
        texto = _ENTIDADE.sub(lambda m: chr(int(m.group(1))), texto)
    return texto


def limpa_texto(valor: str | None) -> str | None:
    """Deserializa o HTML e remove caractere de largura zero.

    São duas passagens, e a ordem não é indiferente: `&#8203;` só **vira** um
    caractere de largura zero depois de desserializado. Limpando antes, os
    `\u200b` que já vinham escaped escapariam da limpeza.
    """
    if valor is None:
        return None
    return desserializa_html(str(valor)).translate(_ZERO_WIDTH)


# --------------------------------------------------------------- texto e identidade
def so_digitos(valor: str | None) -> str:
    """Extrai apenas dígitos, preservando zeros à esquerda do documento."""
    if valor is None:
        return ""
    return _DIGITS.sub("", str(valor))


def normaliza_nome(nome: str | None) -> str:
    """Maiúsculas sem acento, para casar nomes que a ANAC grafou de formas distintas."""
    if not nome:
        return ""
    txt = unicodedata.normalize("NFKD", str(nome))
    txt = "".join(c for c in txt if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", txt).strip().upper()


def normaliza_uf(uf: str | None) -> str | None:
    if not uf:
        return None
    txt = unicodedata.normalize("NFKD", str(uf))
    txt = "".join(c for c in txt if not unicodedata.combining(c)).strip().upper()
    return None if txt in _SEM_DOCUMENTO else txt[:2]


def classifica_documento(bruto: str | None) -> tuple[str, str | None, bool, bool]:
    """Retorna (natureza, documento_limpo, mascarado, invalido).

    - CNPJ com 14 dígitos e prefixo 0  -> JURIDICA, identidade confiável.
    - CPF com 11 dígitos                -> FISICA, identidade confiável.
    - Máscara                          -> mascarado; a natureza vem do formato
                                          (`NNN.NNN.NNN-NN` é CPF, 14 dígitos é
                                          CNPJ) e o documento não é recuperável.
    - `Indisponível` ou vazio           -> natureza desconhecida, documento NULL.

    A ANAC mascara CPF de dois jeitos: com `XXX` no RAB antigo (`062.XXX.XXX-89`)
    e com `*` no SISANT (`CPF: ****525.372***`). Os dois precisam ser reconhecidos,
    senão um CPF mascarado vira empresa por engano.
    """
    if bruto is None:
        return "JURIDICA", None, False, True

    txt = str(bruto).strip()
    if txt.lower() in _SEM_DOCUMENTO:
        return "JURIDICA", None, False, True

    digitos = so_digitos(txt)
    tem_rotulo_cpf = "cpf" in txt.lower()
    tem_rotulo_cnpj = "cnpj" in txt.lower()

    # `000.XXX.XXX-00` é o placeholder de "documento zerado", não uma pessoa.
    if set(digitos) == {"0"}:
        return "JURIDICA", None, False, True

    mascarado = ("*" in txt) or bool(re.search(r"X{2,}", txt))
    if mascarado:
        # Formato `NNN.NNN.NNN-NN` identifica CPF mesmo mascarado; a barra
        # identifica CNPJ.
        parece_cpf = bool(re.search(r"\d{3}\.X+\.X+-\d{2}$", txt)) or tem_rotulo_cpf
        parece_cnpj = "/" in txt or tem_rotulo_cnpj
        if parece_cnpj and not parece_cpf:
            return "JURIDICA", None, True, False
        return "FISICA", None, True, False

    if len(digitos) == 14:
        return "JURIDICA", digitos, False, False
    if len(digitos) == 11:
        return "FISICA", digitos, False, False

    if tem_rotulo_cpf:
        return "FISICA", None, False, True
    if tem_rotulo_cnpj:
        return "JURIDICA", None, False, True
    return "JURIDICA", None, False, True


def chave_pessoa(natureza: str, documento: str | None, nome: str, uf: str | None) -> str:
    """Chave de identidade estável.

    Documento íntegro manda: `PJ:<cnpj>` / `PF:<cpf>`. Sem documento, cai para
    nome normalizado + UF, que é a melhor granularidade que a fonte permite.
    Sem nome também, cai para `DESCONHECIDO` para não criar identidade falsa.
    """
    if documento:
        return f"{'PJ' if natureza == 'JURIDICA' else 'PF'}:{documento}"
    # `limpa_texto` antes de normalizar: sem isso a chave carrega a entidade
    # HTML do nome, e a mesma empresa voltaria como duas pessoas diferentes
    # conforme o arquivo de origem.
    base = normaliza_nome(limpa_texto(nome))
    if not base:
        return "DESCONHECIDO"
    return f"NOME:{base}|{normaliza_uf(uf) or ''}"


# ------------------------------------------------------------------ leitura de arquivo
def read_delimited(path: Path) -> tuple[list[str], list[list[str]]]:
    """Lê CSV/XLS exportado como texto, detectando encoding e delimitador.

    Devolve (cabeçalho, linhas). Linhas vazias e a linha de preâmbulo
    `Atualizado em: ...` que a ANAC antepõe ao cabeçalho são removidas.
    """
    if not path.exists():
        raise FileNotFoundError(path)

    texto = None
    for enc in ("utf-8-sig", "latin-1"):
        try:
            texto = path.read_text(encoding=enc)
            break
        except UnicodeDecodeError:
            continue
    if texto is None:
        raise UnicodeDecodeError("csv", b"", 0, 1, f"nao decodificou: {path.name}")

    # A ANAC usa ';' em quase tudo, mas alguns arquivos vieram com ','.
    try:
        sniff = csv.Sniffer().sniff(texto[:8000], delimiters=";,")
        delim = sniff.delimiter
    except csv.Error:
        delim = ";" if texto[:8000].count(";") > texto[:8000].count(",") else ","

    linhas = list(csv.reader(texto.splitlines(), delimiter=delim, quotechar='"'))
    linhas = [ln for ln in linhas if any(c.strip() for c in ln)]

    # Acha o cabeçalho real pulando a linha de metadados em coluna única.
    inicio = _achar_cabecalho(linhas)

    cabecalho = [c.strip().lstrip("\ufeff") for c in linhas[inicio]]
    corpo = linhas[inicio + 1:]
    return cabecalho, corpo


def _achar_cabecalho(linhas: list[list[str]], limite: int = 6) -> int:
    """Índice da linha de cabeçalho real.

    A ANAC antepõe metadados em coluna única — `Atualizado em: ...` em uns
    arquivos, `Criado em: ...` em outros. Regra: a primeira linha com duas ou
    mais células preenchidas é o cabeçalho, porque metadado ocupa uma só.
    """
    for i, ln in enumerate(linhas[:limite]):
        preenchidas = [c for c in ln if c.strip()]
        if len(preenchidas) >= 2:
            return i
    return 0


def repair_json_anac(bruto: str) -> str:
    """Desfaz a corrupção de escape da exportação da ANAC.

    O export serializa as aspas internas invertendo a barra e duplicando a
    aspa: `\\"NOME\\"` vira `/\\"\\"NOME/\\"`. A correção é determinística e
    verificada por `json.loads` na sequência; se ainda assim não parsear, o
    chamador registra o arquivo como falho em vez de seguir com lixo.
    """
    return bruto.replace('/\\"\\"', '\\"').replace('/""', '"')


def read_json_anac(path: Path) -> list[dict]:
    """Lê um snapshot JSON da ANAC reparando os escapes corrompidos."""
    bruto = path.read_text(encoding="utf-8-sig")
    dados = json.loads(repair_json_anac(bruto))
    if not isinstance(dados, list):
        raise ValueError(f"{path.name}: esperava lista, veio {type(dados).__name__}")
    return dados
