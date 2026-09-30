' bridge.vbs -- dump an Access database (.accdb / .mdb) to TSV files.
'
'   cscript //nologo bridge.vbs tables <accdb>            list user tables (one per line)
'   cscript //nologo bridge.vbs dump   <accdb> <outdir>   write <outdir>\_schema.tsv and <table>.tsv
'
' Runs under Windows Script Host with ADODB + Microsoft ACE OLE DB 12.0
' (the same provider the VBA version used). Python (dbviewer/accdb_bridge/importer.py)
' starts this with cscript.exe and reads the files back, so no extra Python
' package is needed on the line PCs.
'
' File format (all files UTF-16LE with BOM, CRLF):
'   _schema.tsv   table <TAB> column <TAB> ado_type <TAB> defined_size
'   <table>.tsv   first line = column names, then one record per line.
'                 cell escaping: \t \n \r \\ ; NULL = \N
'   dates are written as yyyy/mm/dd hh:nn:ss (yyyy/mm/dd when the time is 00:00:00)
'
' This file is ASCII only on purpose: WSH reads .vbs in the system ANSI code page.
Option Explicit

Const adSchemaTables = 20
Const adOpenStatic = 3
Const adLockReadOnly = 1
Const adUseClient = 3
Const adDate = 7
Const adDBDate = 133
Const adDBTime = 134
Const adDBTimeStamp = 135
Const adBoolean = 11
Const adLongVarBinary = 205
Const adBinary = 128
Const adVarBinary = 204

Dim args: Set args = WScript.Arguments
If args.Count < 2 Then
  WScript.StdErr.WriteLine "usage: bridge.vbs tables <accdb> | dump <accdb> <outdir>"
  WScript.Quit 2
End If

Dim mode: mode = LCase(args(0))
Dim dbPath: dbPath = args(1)
Dim fso: Set fso = CreateObject("Scripting.FileSystemObject")
If Not fso.FileExists(dbPath) Then
  WScript.StdErr.WriteLine "file not found: " & dbPath
  WScript.Quit 3
End If

Dim cn: Set cn = OpenConnection(dbPath)

Select Case mode
  Case "tables"
    Dim t
    For Each t In UserTables(cn)
      WScript.StdOut.WriteLine t
    Next
  Case "dump"
    If args.Count < 3 Then
      WScript.StdErr.WriteLine "dump needs <outdir>"
      WScript.Quit 2
    End If
    DumpAll cn, args(2)
  Case Else
    WScript.StdErr.WriteLine "unknown mode: " & mode
    WScript.Quit 2
End Select

cn.Close
WScript.Quit 0

' ------------------------------------------------------------------
Function OpenConnection(path)
  Dim ext: ext = LCase(fso.GetExtensionName(path))
  Dim prov
  Select Case ext
    Case "accdb": prov = "Microsoft.ACE.OLEDB.12.0"
    Case "mdb":   prov = "Microsoft.Jet.OLEDB.4.0"
    Case Else:    prov = "Microsoft.ACE.OLEDB.12.0"
  End Select
  Dim c: Set c = CreateObject("ADODB.Connection")
  c.Provider = prov
  c.Open path
  c.CursorLocation = adUseClient
  Set OpenConnection = c
End Function

Function UserTables(c)
  ' Same rule as the VBA version (GetTblDic): TABLE type only, MSys* excluded.
  Dim rs: Set rs = c.OpenSchema(adSchemaTables)
  Dim names: names = Array()
  Dim n: n = 0
  Do Until rs.EOF
    If rs("TABLE_TYPE").Value = "TABLE" Then
      Dim nm: nm = rs("TABLE_NAME").Value
      If Left(nm, 4) <> "MSys" And Left(nm, 2) <> "f_" And Left(nm, 1) <> "~" Then
        ReDim Preserve names(n)
        names(n) = nm
        n = n + 1
      End If
    End If
    rs.MoveNext
  Loop
  rs.Close
  UserTables = names
End Function

Sub DumpAll(c, outDir)
  If Not fso.FolderExists(outDir) Then fso.CreateFolder outDir
  Dim schema: Set schema = fso.CreateTextFile(fso.BuildPath(outDir, "_schema.tsv"), True, True)
  Dim t
  For Each t In UserTables(c)
    DumpTable c, t, outDir, schema
  Next
  schema.Close
End Sub

Sub DumpTable(c, tableName, outDir, schema)
  Dim rs: Set rs = CreateObject("ADODB.Recordset")
  rs.Open "SELECT * FROM [" & tableName & "]", c, adOpenStatic, adLockReadOnly
  Dim f: Set f = fso.CreateTextFile(fso.BuildPath(outDir, SafeName(tableName) & ".tsv"), True, True)
  Dim i, line, cnt: cnt = rs.Fields.Count
  ' header + schema
  line = ""
  For i = 0 To cnt - 1
    schema.WriteLine tableName & vbTab & rs.Fields(i).Name & vbTab & rs.Fields(i).Type & vbTab & rs.Fields(i).DefinedSize
    If i > 0 Then line = line & vbTab
    line = line & Esc(rs.Fields(i).Name)
  Next
  f.WriteLine line
  Do Until rs.EOF
    line = ""
    For i = 0 To cnt - 1
      If i > 0 Then line = line & vbTab
      line = line & CellText(rs.Fields(i))
    Next
    f.WriteLine line
    rs.MoveNext
  Loop
  f.Close
  rs.Close
End Sub

Function CellText(fld)
  Dim v: v = fld.Value
  If IsNull(v) Then
    CellText = "\N"
    Exit Function
  End If
  Select Case fld.Type
    Case adDate, adDBDate, adDBTimeStamp
      If Hour(v) = 0 And Minute(v) = 0 And Second(v) = 0 Then
        CellText = Pad(Year(v), 4) & "/" & Pad(Month(v), 2) & "/" & Pad(Day(v), 2)
      Else
        CellText = Pad(Year(v), 4) & "/" & Pad(Month(v), 2) & "/" & Pad(Day(v), 2) & " " & _
                   Pad(Hour(v), 2) & ":" & Pad(Minute(v), 2) & ":" & Pad(Second(v), 2)
      End If
    Case adBoolean
      ' Access stores True as -1. Keep the number so SQL comparisons stay the same.
      If v Then CellText = "-1" Else CellText = "0"
    Case adLongVarBinary, adBinary, adVarBinary
      CellText = "\N"
    Case Else
      CellText = Esc(CStr(v))
  End Select
End Function

Function Esc(s)
  s = Replace(s, "\", "\\")
  s = Replace(s, vbTab, "\t")
  s = Replace(s, vbCr, "\r")
  s = Replace(s, vbLf, "\n")
  Esc = s
End Function

Function Pad(n, width)
  Pad = Right(String(width, "0") & CStr(n), width)
End Function

Function SafeName(s)
  ' table names may contain characters that are illegal in file names
  Dim bad: bad = Array("\", "/", ":", "*", "?", """", "<", ">", "|")
  Dim b
  For Each b In bad
    s = Replace(s, b, "_")
  Next
  SafeName = s
End Function
