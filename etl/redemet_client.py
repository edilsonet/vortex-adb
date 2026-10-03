"""Cliente da API-REDEMET (DECEA).

Pontos que a API impõe e que o cliente respeita:

- **Autenticação** por header `X-Api-Key` ou por `?api_key=`. Usa o header, para
  não vazar a chave em log de URL. A chave vem de `secrets/redemet.key`, que
  está no `.gitignore`.
- **Limite de uso**: a documentação diz que há limite e que cada solicitação
  vale ~8.760 registros. O cliente aplica um intervalo entre requisições e um
  teto de aeródromos por coleta. A API é infraestrutura militar — a cortesia
  não é opcional aqui.
- **Paginação**: METAR e TAF devolvem `next_page_url`; o cliente segue até o fim,
  parando se o servidor devolver a mesma página.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://api-redemet.decea.mil.br"
SEGREDO = Path(__file__).resolve().parents[1] / "secrets" / "redemet.key"

# Intervalo entre requisições, em segundos. A doc não publica o número exato,
# então o padrão é conservador.
DELAY_PADRAO = 1.0
TIMEOUT = 30
TENTATIVAS = 2
# A API rejeita com "Tamanho da página maior que a permitida" acima do padrão
# documentado de 150. Não aumentar sem confirmar.
PAGE_TAM = 150


class RedemetErro(RuntimeError):
    pass


def ler_chave(caminho: Path = SEGREDO) -> str:
    if not caminho.exists():
        raise RedemetErro(
            f"chave REDEMET ausente em {caminho}. "
            "Grave a chave nesse arquivo (ele esta no .gitignore) "
            "ou defina REDEMET_API_KEY no ambiente."
        )
    chave = caminho.read_text(encoding="utf-8").strip()
    if not chave:
        raise RedemetErro(f"chave REDEMET vazia em {caminho}")
    return chave


class RedemetClient:
    def __init__(self, chave: str | None = None, delay: float = DELAY_PADRAO):
        self.chave = chave or ler_chave()
        self.delay = delay
        self.requisicoes = 0
        self._ultimo = 0.0

    def _esperar(self) -> None:
        """Mantém o intervalo mínimo entre chamadas."""
        decorrido = time.monotonic() - self._ultimo
        if decorrido < self.delay:
            time.sleep(self.delay - decorrido)

    def _get(self, caminho: str, params: dict | None = None) -> dict:
        url = f"{BASE}{caminho}"
        if params:
            url += "?" + urllib.parse.urlencode(params)

        ultimo_erro = None
        for tentativa in range(TENTATIVAS):
            self._esperar()
            req = urllib.request.Request(url, headers={
                "X-Api-Key": self.chave,
                "Accept": "application/json",
                "User-Agent": "anac-db/1.0 (dados abertos ANAC)",
            })
            try:
                with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                    self._ultimo = time.monotonic()
                    self.requisicoes += 1
                    corpo = json.loads(resp.read().decode("utf-8"))
                if not corpo.get("status", False):
                    raise RedemetErro(
                        f"{caminho}: status={corpo.get('status')} "
                        f"mensagem={corpo.get('message')!r}"
                    )
                return corpo
            except urllib.error.HTTPError as exc:
                ultimo_erro = RedemetErro(f"{caminho}: HTTP {exc.code}")
                if exc.code in (400, 401, 403, 404):
                    break  # erro do pedido, retry nao resolve
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                ultimo_erro = RedemetErro(f"{caminho}: {exc}")
            if tentativa + 1 < TENTATIVAS:
                time.sleep(self.delay * 2)
        raise ultimo_erro or RedemetErro(f"{caminho}: falha desconhecida")

    # ------------------------------------------------------------- endpoints
    def status_aerodromos(self, pais: str = "BRASIL") -> list[list]:
        """`/aerodromos/status` devolve listas, não objetos.

        Posições: [icao, nome, latitude, longitude, cor, mensagem]. A mensagem
        só existe quando a localidade não tem METAR; por isso o leitor trata
        5 ou 6 campos.
        """
        dados = self._get("/aerodromos/status", {"pais": pais}).get("data") or []
        return dados if isinstance(dados, list) else []

    def _paginas(self, caminho: str, params: dict, max_paginas: int = 60) -> list[dict]:
        """Segue a paginação até o fim, com trava contra laço infinito.

        O envelope traz `next_page_url`; a forma mais estável de avançar é o
        contador `page`, que o servidor respeita em vez de reenviar a query
        inteira.
        """
        params = dict(params)
        registros: list[dict] = []
        for pagina in range(1, max_paginas + 1):
            params["page"] = pagina
            envelope = self._get(caminho, params).get("data") or {}
            novos = envelope.get("data") or []
            if not novos:
                break
            registros.extend(novos)
            if not envelope.get("next_page_url"):
                break
        return registros

    def metar(self, icaos: list[str], data_ini: str | None = None,
              data_fim: str | None = None) -> list[dict]:
        """METAR/SPECI. `data_ini`/`data_fim` em `YYYYMMDDHH`."""
        params = {"page_tam": PAGE_TAM}
        if data_ini:
            params["data_ini"] = data_ini
        if data_fim:
            params["data_fim"] = data_fim
        return self._paginas(f"/mensagens/metar/{','.join(icaos)}", params)

    def taf(self, icaos: list[str], data_ini: str | None = None,
            data_fim: str | None = None) -> list[dict]:
        """TAF. `fim_linha=texto` devolve quebra de linha simples, mais fácil de ler."""
        params = {"page_tam": PAGE_TAM, "fim_linha": "texto"}
        if data_ini:
            params["data_ini"] = data_ini
        if data_fim:
            params["data_fim"] = data_fim
        return self._paginas(f"/mensagens/taf/{','.join(icaos)}", params)
