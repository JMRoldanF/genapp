       IDENTIFICATION DIVISION.
       PROGRAM-ID. CPYFLOW.
      * NOM-2507: OWN-REJECT is reached only through a paragraph the
      * copybook FLOWPARA defines; OWN-DEAD is reached by nothing.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01  WS-AMOUNT                 PIC S9(7)V99 VALUE -5.
       01  WS-TOTAL                  PIC S9(9)V99 VALUE 0.
       PROCEDURE DIVISION.
       MAIN-PARA.
           PERFORM COPY-VALIDATE
           PERFORM COPY-TOTAL
           DISPLAY WS-TOTAL
           GOBACK.
       OWN-REJECT.
           DISPLAY 'REJECTED'
           MOVE 0 TO WS-AMOUNT.
       OWN-DEAD.
           DISPLAY 'NEVER REACHED'.
           COPY FLOWPARA.
