"""Access(.accdb / .mdb)→ SQLite(.sqlite3)の取り込み

    Python(importer.py) ── subprocess ──▶ cscript.exe bridge.vbs
                                              │ ADODB.Connection (COM)
                                              │ Microsoft ACE OLE DB 12.0
                                              ▼
                                          .accdb  ──▶ TSV(UTF-16LE)
    Python ◀── TSV を読む ── sqlite3 に書く(一時ファイル → 差し替え)

ライン PC の Python に追加パッケージを入れられないため、Access を読む
部分は Windows に最初から入っている cscript + ADODB + ACE に任せる。
"""
