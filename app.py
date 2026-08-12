"""Entry point — GUI bez argumentow, CLI z argumentami.

  Lodziarz.exe                  -> okno GUI z viewerem
  Lodziarz.exe gui --browser    -> serwer + przegladarka
  Lodziarz.exe process ...      -> batch CLI
"""
import multiprocessing
import sys


def main() -> int:
    multiprocessing.freeze_support()  # PyInstaller onefile
    from lodziarz.cli import main as cli_main
    return cli_main()


if __name__ == "__main__":
    sys.exit(main())
