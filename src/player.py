from __future__ import annotations

import argparse
from pathlib import Path


class RockMachinePlayer:
    """Utilitário leve para inspecionar os stems processados pelo backend."""

    def __init__(self, temp_dir: str | Path = "../temp"):
        self.temp_dir = Path(temp_dir).resolve()

    def listar_stems_processados(self) -> list[Path]:
        if not self.temp_dir.exists():
            return []
        return sorted(self.temp_dir.glob("*.wav"))

    def resolver_caminho(self, nome_arquivo: str) -> Path:
        return self.temp_dir / nome_arquivo

    def existe(self, nome_arquivo: str) -> bool:
        return self.resolver_caminho(nome_arquivo).exists()


def main() -> None:
    parser = argparse.ArgumentParser(description="Utilitário da Rock Machine para validar arquivos processados.")
    parser.add_argument("--temp-dir", default="../temp", help="Diretório onde o backend salva os WAVs processados.")
    args = parser.parse_args()

    player = RockMachinePlayer(temp_dir=args.temp_dir)
    arquivos = player.listar_stems_processados()

    print(f"Diretório temporário: {player.temp_dir}")
    if not arquivos:
        print("Nenhum stem processado encontrado.")
        return

    print("Stems disponíveis:")
    for arquivo in arquivos:
        print(f"- {arquivo.name}")


if __name__ == "__main__":
    main()