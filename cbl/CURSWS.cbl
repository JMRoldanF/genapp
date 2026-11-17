       IDENTIFICATION DIVISION.
       PROGRAM-ID. CURSWS.
      * NOM-2081: cursor declared in WORKING-STORAGE; OPEN and FETCH
      * name no table.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
           EXEC SQL INCLUDE SQLCA END-EXEC.
       01  WS-ACCT-ID                PIC 9(10).
       01  WS-ACCT-BAL               PIC S9(11)V99 COMP-3.
           EXEC SQL
               DECLARE ACCT-CUR CURSOR FOR
               SELECT ACCT_ID, ACCT_BALANCE
                 FROM QAV1923.ACCOUNT_MASTER
                WHERE ACCT_STATUS = 'A'
           END-EXEC.
       PROCEDURE DIVISION.
       MAIN-PARA.
           EXEC SQL OPEN ACCT-CUR END-EXEC
           PERFORM FETCH-PARA UNTIL SQLCODE NOT = 0
           EXEC SQL CLOSE ACCT-CUR END-EXEC
           GOBACK.
       FETCH-PARA.
           EXEC SQL
               FETCH ACCT-CUR INTO :WS-ACCT-ID, :WS-ACCT-BAL
           END-EXEC
           IF SQLCODE = 0
               DISPLAY WS-ACCT-ID
           END-IF.
