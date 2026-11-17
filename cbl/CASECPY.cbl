       IDENTIFICATION DIVISION.
       PROGRAM-ID. CASECPY.
      * NOM-2806: COPY names the member in another case than its file.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
           COPY m1c REPLACING ==:TAG:== BY ==WS==.
       PROCEDURE DIVISION.
       MAIN-PARA.
           MOVE 12345678 TO WS-CUST-ID
           MOVE 'ACME LTD' TO WS-CUST-NAME
           ADD 100 TO WS-BALANCE
           DISPLAY WS-CUST-NAME
           GOBACK.
