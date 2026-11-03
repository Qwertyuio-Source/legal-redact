on open theFiles
    set homePath to POSIX path of (path to home folder)
    set binPath to homePath & "Library/Application Support/LegalRedact/legalredact"
    set okCount to 0
    set failList to ""
    repeat with f in theFiles
        set p to POSIX path of f
        try
            set d to do shell script "dirname " & quoted form of p
            do shell script quoted form of binPath & " " & quoted form of p & " -o " & quoted form of d
            set okCount to okCount + 1
        on error errMsg
            set failList to failList & p & "：" & errMsg & linefeed
        end try
    end repeat
    if failList is "" then
        display notification ("已处理 " & okCount & " 个文件，结果在原文件旁边") with title "法律文书脱敏"
    else
        display dialog "部分失败：" & linefeed & failList buttons {"好"} default button 1 with title "法律文书脱敏"
    end if
end open
