' ============================================================================
' office_pdf.vbs - turn one Excel / Word file into a PDF for the preview
'
'   * ASCII ONLY. Do not add Japanese text to this file (cscript reads it as
'     ANSI). Japanese values (the file path, error messages) travel as hex
'     (UTF-16 code units, 4 hex digits each) instead.
'   * Called by dbviewer/office_bridge/__init__.py, once per preview:
'       cscript //nologo //E:vbscript office_pdf.vbs
'   * The way of the inspection sheet tool (ViVio-Hellon/all-tools,
'     tools/inspection/app/vbscript/excel_worker.vbs), which runs on the line
'     PCs: a hidden Excel / Word opens the file READ-ONLY and writes a PDF with
'     ExportAsFixedFormat into THIS SCRIPT'S OWN TEMP folder; the PDF is sent
'     to Python as base64 lines on stdout and deleted at once.
'   * NO FILES are exchanged with Python. Python from the Microsoft Store
'     writes %LOCALAPPDATA% / %TEMP% to a private copy that cscript and Office
'     cannot see ("path not found", 0x4C). Python never sees the PDF file.
'   * Excel: Visible = False, IgnoreRemoteRequests (a file the user
'     double-clicks never opens in this hidden Excel), DisplayAlerts = False,
'     no link update, events and macros off (AutomationSecurity = force
'     disable), a dummy password (a protected workbook fails instead of
'     waiting for a password in a window nobody sees). Quit at the end.
'     When Excel cannot write a PDF (no printer driver, old Excel), the
'     workbook is saved as .xlsx into the own TEMP instead (XLSXDATA) and
'     Python shows it as tables.
'   * Word: the Word PID is found with WMI before any document is opened
'     (WORDPID; Python ends only that process if it hangs). If CreateObject
'     hands back a Word that was already running (the user's), it is used as
'     it is: not hidden, its settings given back, never quit (WORDSHARED).
'     Documents.Open(path, ConfirmConversions:=False, ReadOnly:=True,
'     AddToRecentFiles:=False, PasswordDocument:=dummy, ..., Visible:=False).
'     Word is quit at the end unless documents of the user are open in it.
'     When Word cannot write a PDF, the document is saved as .docx into the
'     own TEMP (DOCXDATA) and Python shows it as text.
'   * Leftovers of an ended worker (dbv_pv_* older than an hour) are deleted
'     when the worker starts.
'
' Protocol
'   stdin (from Python)
'     SET <key> <hex>     (path, kind = excel | word, max_pages, password)
'     ENDJOB
'   stdout (ASCII, one command per line)
'     READY
'     STAGE <name>        (the step that starts now: excel.open, word.pdf, ...)
'     EXCEL <hwnd>        (Python records the Excel PID from this)
'     WORDPID <pid>       (the Word just started; 0 is never sent)
'     WORDSHARED 1        (the user's running Word: Python must never end it)
'     PDFDATA <base64>    (several lines)   then  DONE pdf
'     XLSXDATA <base64>   (several lines)   then  DONE xlsx
'     DOCXDATA <base64>   (several lines)   then  DONE docx
'     RESULT <key> <hex>  (code, errno, message, step, log, pages)
'     FINISH <rc>         (0 = sent, 3 = could not)
' ============================================================================
Option Explicit

Const msoAutomationSecurityForceDisable = 3
Const adTypeBinary = 1
Const TemporaryFolder = 2
Const xlTypePDF = 0
Const xlQualityMinimum = 1
Const xlOpenXMLWorkbook = 51
Const wdAlertsNone = 0
Const wdDoNotSaveChanges = 0
Const wdStatisticPages = 2
Const wdExportFormatPDF = 17
Const wdExportOptimizeForOnScreen = 1
Const wdExportAllDocument = 0
Const wdExportFromTo = 3
Const wdExportDocumentContent = 0
Const wdExportCreateNoBookmarks = 0
Const wdFormatXMLDocument = 16

Const RC_OK = 0
Const RC_ARGS = 2
Const RC_FAILED = 3

Dim gFso, gJob, gXl, gWd, gWdShared, gSavedAlerts, gStage

Set gFso = CreateObject("Scripting.FileSystemObject")
Set gXl = Nothing
Set gWd = Nothing
gWdShared = False
gStage = ""

WScript.Quit Main()


Function Main()
    Dim rc, kind, path
    SweepTempFiles
    Set gJob = ReadJob()
    Emit "READY"
    kind = LCase(JobValue("kind", ""))
    path = JobValue("path", "")
    If Len(path) = 0 Then
        Fail "BAD_JOB", 0, "no path"
        rc = RC_ARGS
    ElseIf Not gFso.FileExists(path) Then
        Fail "FILE_NOT_FOUND", 0, path
        rc = RC_FAILED
    ElseIf kind = "excel" Then
        rc = RunExcel(path)
    ElseIf kind = "word" Then
        rc = RunWord(path)
    Else
        Fail "BAD_JOB", 0, "kind: " & kind
        rc = RC_ARGS
    End If
    QuitExcel
    FinishWord
    Emit "FINISH " & rc
    Main = rc
End Function


' ----------------------------------------------------------------------------
' Excel
' ----------------------------------------------------------------------------
Function RunExcel(path)
    Dim wb, outPath, number, description, maxPages, sent

    RunExcel = RC_FAILED
    If Not StartExcel() Then Exit Function
    Set wb = OpenBook(path)
    If wb Is Nothing Then Exit Function

    maxPages = 0
    On Error Resume Next
    maxPages = CLng(JobValue("max_pages", "0"))
    Err.Clear

    ' ExportAsFixedFormat(Type, Filename, Quality, IncludeDocProperties,
    '   IgnorePrintAreas, From, To, OpenAfterPublish)
    outPath = NewTempPath(".pdf")
    Stage "excel.pdf"
    Err.Clear
    If maxPages > 0 Then
        wb.ExportAsFixedFormat xlTypePDF, outPath, xlQualityMinimum, False, False, 1, maxPages, False
    Else
        wb.ExportAsFixedFormat xlTypePDF, outPath, xlQualityMinimum, False, False, , , False
    End If
    number = Err.Number
    description = Err.Description
    Err.Clear
    If number <> 0 And maxPages > 0 Then
        ' fewer pages than From/To on some versions: try the whole workbook
        NoteLog "Workbook.ExportAsFixedFormat (pages 1-" & maxPages & ") failed: 0x" & Hex(number) & " " & description
        DeleteTempFile outPath
        Err.Clear
        wb.ExportAsFixedFormat xlTypePDF, outPath, xlQualityMinimum, False, False, , , False
        number = Err.Number
        description = Err.Description
        Err.Clear
    End If
    If number = 0 Then
        sent = SendFile(outPath, "PDFDATA")
        DeleteTempFile outPath
        If sent Then
            Emit "DONE pdf"
            CloseBook wb
            RunExcel = RC_OK
            Exit Function
        End If
    Else
        NoteLog "Workbook.ExportAsFixedFormat (PDF) failed: 0x" & Hex(number) & " " & description
        DeleteTempFile outPath
    End If

    ' No PDF (no printer driver, old Excel): the workbook as .xlsx instead
    outPath = NewTempPath(".xlsx")
    Stage "excel.xlsx"
    Err.Clear
    wb.SaveAs outPath, xlOpenXMLWorkbook
    number = Err.Number
    description = Err.Description
    Err.Clear
    CloseBook wb
    If number <> 0 Then
        DeleteTempFile outPath
        Fail "EXPORT_FAILED", number, description
        Exit Function
    End If
    sent = SendFile(outPath, "XLSXDATA")
    DeleteTempFile outPath
    If Not sent Then
        Fail "EXPORT_FAILED", 0, "the file was not written or could not be read"
        Exit Function
    End If
    Emit "DONE xlsx"
    RunExcel = RC_OK
End Function


Function StartExcel()
    Dim hwnd, number, description

    StartExcel = False
    Stage "excel.create"
    On Error Resume Next
    Err.Clear
    Set gXl = CreateObject("Excel.Application")
    number = Err.Number
    description = Err.Description
    Err.Clear
    If number <> 0 Or gXl Is Nothing Then
        Set gXl = Nothing
        Fail "EXCEL_CREATE_FAILED", number, description
        Exit Function
    End If
    ' The window handle first, so that Python can always clean up
    hwnd = 0
    hwnd = gXl.Hwnd
    Err.Clear
    Emit "EXCEL " & CStr(hwnd)
    Stage "excel.setup"
    gXl.Visible = False
    ' Files the user double-clicks must not open in this hidden instance.
    ' Reset before Quit: Excel saves this option (QuitExcel).
    gXl.IgnoreRemoteRequests = True
    gXl.DisplayAlerts = False
    gXl.AskToUpdateLinks = False
    gXl.ScreenUpdating = False
    gXl.EnableEvents = False
    gXl.AutomationSecurity = msoAutomationSecurityForceDisable
    Err.Clear
    WriteResult "excel.version", gXl.Version
    Err.Clear
    StartExcel = True
End Function


' Workbooks.Open(FileName, UpdateLinks, ReadOnly, Format, Password,
'   WriteResPassword, IgnoreReadOnlyRecommended, Origin, Delimiter,
'   Editable, Notify, Converter, AddToMru)
' A dummy password: a protected workbook fails with an error instead of
' waiting for input in the hidden Excel.
Function OpenBook(path)
    Dim wb, attempt, number, description, pwd

    Set OpenBook = Nothing
    pwd = JobValue("password", "")
    Stage "excel.open"
    On Error Resume Next
    For attempt = 1 To 3
        Err.Clear
        Set wb = Nothing
        Set wb = gXl.Workbooks.Open(path, 0, True, , pwd, , True, , , False, False, , False)
        number = Err.Number
        description = Err.Description
        Err.Clear
        If number = 0 Then Exit For
        If Not IsBusyError(number) Then Exit For
        WScript.Sleep 1000 * attempt
    Next
    If number = 0 And wb Is Nothing Then
        number = -1
        description = "Workbooks.Open returned Nothing"
    End If
    If number <> 0 Then
        Fail "OPEN_FAILED", number, description
        Exit Function
    End If
    Set OpenBook = wb
End Function


Sub CloseBook(wb)
    On Error Resume Next
    If Not wb Is Nothing Then wb.Close False
    Err.Clear
End Sub


Sub QuitExcel()
    Dim i
    On Error Resume Next
    If gXl Is Nothing Then Exit Sub
    Stage "excel.quit"
    gXl.DisplayAlerts = False
    For i = gXl.Workbooks.Count To 1 Step -1
        gXl.Workbooks(i).Close False
    Next
    gXl.IgnoreRemoteRequests = False
    gXl.Quit
    Set gXl = Nothing
    Err.Clear
End Sub


' ----------------------------------------------------------------------------
' Word
' ----------------------------------------------------------------------------
Function RunWord(path)
    Dim doc, outPath, number, description, pages, lastPage, sent

    RunWord = RC_FAILED
    If Not StartWord() Then Exit Function
    Set doc = OpenDoc(path)
    If doc Is Nothing Then Exit Function

    On Error Resume Next
    pages = 0
    pages = CLng(doc.ComputeStatistics(wdStatisticPages))
    lastPage = 0
    lastPage = CLng(JobValue("max_pages", "0"))
    Err.Clear
    WriteResult "pages", CStr(pages)

    ' ExportAsFixedFormat(OutputFileName, ExportFormat, OpenAfterExport,
    '   OptimizeFor, Range, From, To, Item, IncludeDocProps, KeepIRM,
    '   CreateBookmarks, DocStructureTags, BitmapMissingFonts, UseISO19005_1)
    outPath = NewTempPath(".pdf")
    Stage "word.pdf"
    Err.Clear
    If lastPage > 0 And pages > lastPage Then
        doc.ExportAsFixedFormat outPath, wdExportFormatPDF, False, wdExportOptimizeForOnScreen, wdExportFromTo, 1, lastPage, wdExportDocumentContent, False, True, wdExportCreateNoBookmarks, False, True, False
    Else
        doc.ExportAsFixedFormat outPath, wdExportFormatPDF, False, wdExportOptimizeForOnScreen, wdExportAllDocument, 1, 1, wdExportDocumentContent, False, True, wdExportCreateNoBookmarks, False, True, False
    End If
    number = Err.Number
    description = Err.Description
    Err.Clear
    If number = 0 Then
        sent = SendFile(outPath, "PDFDATA")
        DeleteTempFile outPath
        If sent Then
            Emit "DONE pdf"
            CloseDoc doc
            RunWord = RC_OK
            Exit Function
        End If
    Else
        NoteLog "Document.ExportAsFixedFormat (PDF) failed: 0x" & Hex(number) & " " & description
        DeleteTempFile outPath
        If WordGone() Then
            Fail "WORD_GONE", number, description
            Exit Function
        End If
    End If

    ' No PDF (old Word): the document as .docx instead
    outPath = NewTempPath(".docx")
    Stage "word.docx"
    Err.Clear
    doc.SaveAs outPath, wdFormatXMLDocument
    number = Err.Number
    description = Err.Description
    Err.Clear
    CloseDoc doc
    If number <> 0 Then
        DeleteTempFile outPath
        Fail "EXPORT_FAILED", number, description
        Exit Function
    End If
    sent = SendFile(outPath, "DOCXDATA")
    DeleteTempFile outPath
    If Not sent Then
        Fail "EXPORT_FAILED", 0, "the file was not written or could not be read"
        Exit Function
    End If
    Emit "DONE docx"
    RunWord = RC_OK
End Function


Function StartWord()
    Dim number, description, before, pid, vis, docs, attempt

    StartWord = False
    On Error Resume Next
    Stage "word.pid"
    Set before = WordProcessIds()
    Stage "word.create"
    Err.Clear
    Set gWd = CreateObject("Word.Application")
    number = Err.Number
    description = Err.Description
    Err.Clear
    If number <> 0 Or gWd Is Nothing Then
        Set gWd = Nothing
        Fail "WORD_CREATE_FAILED", number, description
        Exit Function
    End If
    ' The PID first, so that Python can always clean up - also when Word
    ' blocks on a dialog before any document window exists.
    Stage "word.pid"
    pid = NewWordProcessId(before)
    If pid > 0 Then Emit "WORDPID " & pid

    ' A Word that was already running (the user's) instead of a new one:
    ' not hidden, its settings given back, never quit, never ended by Python.
    gWdShared = False
    If pid = 0 Then
        vis = False
        vis = gWd.Visible
        docs = 0
        docs = gWd.Documents.Count
        Err.Clear
        If vis Or docs > 0 Then gWdShared = True
    End If
    If gWdShared Then
        NoteLog "CreateObject returned a Word that was already running: not hidden, not quit"
        Emit "WORDSHARED 1"
        gSavedAlerts = gWd.DisplayAlerts
        Err.Clear
    End If

    ' A Word that has just started may reject calls for a moment: retry.
    Stage "word.setup"
    For attempt = 1 To 5
        Err.Clear
        gWd.DisplayAlerts = wdAlertsNone
        number = Err.Number
        Err.Clear
        If number = 0 Then Exit For
        If Not IsBusyError(number) Then Exit For
        WScript.Sleep 500
    Next
    If Not gWdShared Then gWd.Visible = False
    Err.Clear
    WriteResult "word.version", gWd.Version
    Err.Clear
    StartWord = True
End Function


' Documents.Open(FileName, ConfirmConversions, ReadOnly, AddToRecentFiles,
'   PasswordDocument, PasswordTemplate, Revert, WritePasswordDocument,
'   WritePasswordTemplate, Format, Encoding, Visible)
' ReadOnly: no "File in Use" dialog, never written. A dummy PasswordDocument:
' a protected document fails instead of asking in a hidden window (it is
' ignored for a document without a password).
Function OpenDoc(path)
    Dim doc, attempt, number, description, pwd

    Set OpenDoc = Nothing
    pwd = JobValue("password", "")
    Stage "word.open"
    On Error Resume Next
    For attempt = 1 To 3
        Err.Clear
        Set doc = Nothing
        Set doc = gWd.Documents.Open(path, False, True, False, pwd, , , , , , , False)
        number = Err.Number
        description = Err.Description
        Err.Clear
        If number = 0 Then Exit For
        NoteLog "Documents.Open failed (" & attempt & "): 0x" & Hex(number) & " " & description
        If Not IsBusyError(number) Then Exit For
        WScript.Sleep 1000 * attempt
    Next
    If number = 0 And doc Is Nothing Then
        number = -1
        description = "Documents.Open returned Nothing"
    End If
    If number <> 0 Then
        Fail "OPEN_FAILED", number, description
        Exit Function
    End If
    Set OpenDoc = doc
End Function


Sub CloseDoc(doc)
    On Error Resume Next
    If Not doc Is Nothing Then doc.Close wdDoNotSaveChanges
    Err.Clear
End Sub


Sub FinishWord()
    Dim openDocs
    If gWd Is Nothing Then Exit Sub
    Stage "word.quit"
    On Error Resume Next
    openDocs = -1
    openDocs = gWd.Documents.Count
    If Err.Number <> 0 Then
        Err.Clear
        Set gWd = Nothing
        Exit Sub
    End If
    If gWdShared Then
        gWd.DisplayAlerts = gSavedAlerts
        Err.Clear
    ElseIf openDocs = 0 Then
        gWd.NormalTemplate.Saved = True
        Err.Clear
        gWd.Quit wdDoNotSaveChanges
        Err.Clear
    Else
        ' a document of the user opened in our Word meanwhile: show it, keep it
        gWd.Visible = True
        Err.Clear
        NoteLog "Word left running: " & openDocs & " document(s) of the user are open"
    End If
    Set gWd = Nothing
    gWdShared = False
End Sub


Function WordGone()
    Dim v
    WordGone = True
    If gWd Is Nothing Then Exit Function
    On Error Resume Next
    v = ""
    v = gWd.Version
    If Err.Number = 0 And Len(v) > 0 Then WordGone = False
    Err.Clear
End Function


' WINWORD.EXE processes that are running now ("pid" -> True).
Function WordProcessIds()
    Dim d, svc, procs, p
    Set d = CreateObject("Scripting.Dictionary")
    Set WordProcessIds = d
    On Error Resume Next
    Set svc = GetObject("winmgmts:{impersonationLevel=impersonate}!\\.\root\cimv2")
    If Err.Number <> 0 Then
        Err.Clear
        Exit Function
    End If
    Set procs = svc.ExecQuery("SELECT ProcessId FROM Win32_Process WHERE Name = 'WINWORD.EXE'")
    For Each p In procs
        d(CStr(p.ProcessId)) = True
    Next
    Err.Clear
End Function


' The Word that CreateObject has just started: a WINWORD.EXE that was not
' there before and was started for automation. 0 = not sure (never guess:
' Python may end this process).
Function NewWordProcessId(before)
    Dim svc, procs, p, found, count, cmdLine

    NewWordProcessId = 0
    found = 0
    count = 0
    On Error Resume Next
    Set svc = GetObject("winmgmts:{impersonationLevel=impersonate}!\\.\root\cimv2")
    If Err.Number <> 0 Then
        Err.Clear
        Exit Function
    End If
    Set procs = svc.ExecQuery("SELECT ProcessId, CommandLine FROM Win32_Process WHERE Name = 'WINWORD.EXE'")
    For Each p In procs
        If Not before.Exists(CStr(p.ProcessId)) Then
            cmdLine = ""
            cmdLine = LCase(p.CommandLine & "")
            If InStr(cmdLine, "embedding") > 0 Or InStr(cmdLine, "automation") > 0 Then
                found = p.ProcessId
                count = count + 1
            End If
        End If
    Next
    Err.Clear
    If count = 1 Then
        NewWordProcessId = found
    Else
        NoteLog "Word PID not identified (" & count & " candidates)"
    End If
End Function


' ----------------------------------------------------------------------------
' Temporary files (this script's own TEMP, never Python's)
' ----------------------------------------------------------------------------
Function TempFolder()
    Dim root, folder
    On Error Resume Next
    root = gFso.GetSpecialFolder(TemporaryFolder).Path
    folder = gFso.BuildPath(root, "dbv_preview")
    If Not gFso.FolderExists(folder) Then gFso.CreateFolder folder
    Err.Clear
    If gFso.FolderExists(folder) Then
        TempFolder = folder
    Else
        TempFolder = root
    End If
End Function


Function NewTempPath(ext)
    Dim folder, attempt, candidate
    NewTempPath = ""
    On Error Resume Next
    folder = TempFolder()
    For attempt = 1 To 10
        candidate = gFso.BuildPath(folder, "dbv_pv_" & Replace(gFso.GetTempName(), ".tmp", "") & ext)
        If Not gFso.FileExists(candidate) Then
            NewTempPath = candidate
            Exit Function
        End If
    Next
    Err.Clear
End Function


' Office may hold the file a moment after writing: try again.
Sub DeleteTempFile(path)
    Dim attempt
    On Error Resume Next
    If Len(path) = 0 Then Exit Sub
    For attempt = 1 To 5
        If Not gFso.FileExists(path) Then Exit Sub
        Err.Clear
        gFso.DeleteFile path, True
        If Err.Number = 0 Then Exit Sub
        Err.Clear
        WScript.Sleep 200 * attempt
    Next
    NoteLog "temporary file left (deleted when the worker starts next): " & path
    Err.Clear
End Sub


' Files an ended worker left (older than an hour: a younger one may belong
' to a worker that is running now).
Sub SweepTempFiles()
    Dim folder, f
    On Error Resume Next
    Set folder = gFso.GetFolder(TempFolder())
    If Err.Number <> 0 Then
        Err.Clear
        Exit Sub
    End If
    For Each f In folder.Files
        If LCase(Left(f.Name, 7)) = "dbv_pv_" Then
            If DateDiff("n", f.DateLastModified, Now) > 60 Then f.Delete True
            Err.Clear
        End If
    Next
    Err.Clear
End Sub


' A binary file as base64 lines "<prefix> <chunk>". ADODB.Stream and MSXML
' are part of Windows.
Function SendFile(path, prefix)
    Dim stm, doc, el, b64, i

    SendFile = False
    On Error Resume Next
    If Len(path) = 0 Then Exit Function
    If Not gFso.FileExists(path) Then Exit Function
    If gFso.GetFile(path).Size = 0 Then Exit Function
    Stage "send"
    Set stm = CreateObject("ADODB.Stream")
    stm.Type = adTypeBinary
    stm.Open
    stm.LoadFromFile path
    Set doc = CreateObject("MSXML2.DOMDocument")
    Set el = doc.createElement("b")
    el.dataType = "bin.base64"
    el.nodeTypedValue = stm.Read
    stm.Close
    b64 = Replace(Replace(el.Text, vbCr, ""), vbLf, "")
    If Err.Number <> 0 Then
        NoteLog "base64 failed: 0x" & Hex(Err.Number) & " " & Err.Description
        Err.Clear
        Exit Function
    End If
    For i = 1 To Len(b64) Step 3000
        Emit prefix & " " & Mid(b64, i, 3000)
    Next
    SendFile = (Len(b64) > 0)
End Function


' ----------------------------------------------------------------------------
' Communication with Python
' ----------------------------------------------------------------------------
Function IsBusyError(number)
    IsBusyError = (number = -2147417846 Or number = -2147418111)
End Function


Sub Emit(text)
    On Error Resume Next
    WScript.StdOut.WriteLine text
    Err.Clear
End Sub


Sub Stage(name)
    gStage = name
    Emit "STAGE " & name
End Sub


Sub NoteLog(text)
    WriteResult "log", text
End Sub


Sub Fail(code, number, description)
    WriteResult "code", code
    If number <> 0 Then WriteResult "errno", "0x" & Hex(number)
    If Len(description) > 0 Then WriteResult "message", description
    If Len(gStage) > 0 Then WriteResult "step", gStage
End Sub


' "SET <key> <hex>" lines, then "ENDJOB".
Function ReadJob()
    Dim d, line, parts
    Set d = CreateObject("Scripting.Dictionary")
    d.CompareMode = 1
    Set ReadJob = d
    On Error Resume Next
    Do
        line = WScript.StdIn.ReadLine
        If Err.Number <> 0 Then
            Err.Clear
            Exit Function
        End If
        line = Trim(line)
        If line = "ENDJOB" Then Exit Function
        parts = Split(line, " ")
        If UBound(parts) >= 1 Then
            If parts(0) = "SET" Then
                If UBound(parts) >= 2 Then
                    d(parts(1)) = UnHexW(parts(2))
                Else
                    d(parts(1)) = ""
                End If
            End If
        End If
    Loop
End Function


Function JobValue(key, defaultValue)
    If gJob.Exists(key) Then
        JobValue = gJob(key)
    Else
        JobValue = defaultValue
    End If
End Function


Sub WriteResult(key, value)
    Dim text
    On Error Resume Next
    text = ""
    text = CStr(value)
    Emit "RESULT " & key & " " & HexW(text)
    Err.Clear
End Sub


' UTF-16 code units as 4 hex digits each ("AB" -> "00410042").
Function HexW(text)
    Dim i, code, s
    s = ""
    For i = 1 To Len(text)
        code = AscW(Mid(text, i, 1))
        If code < 0 Then code = code + 65536
        s = s & Right("000" & Hex(code), 4)
    Next
    HexW = s
End Function


Function UnHexW(text)
    Dim i, s
    s = ""
    For i = 1 To Len(text) - 3 Step 4
        s = s & ChrW(CLng("&H" & Mid(text, i, 4)))
    Next
    UnHexW = s
End Function
