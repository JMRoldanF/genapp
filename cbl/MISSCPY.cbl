       IDENTIFICATION DIVISION.
       PROGRAM-ID. MISSCPY.
      * NOM-2507: copies a procedure-division member the repository
      * does not hold, so its unused-code answer must name it.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01  WS-FLAG                   PIC X VALUE 'N'.
       PROCEDURE DIVISION.
       MAIN-PARA.
           PERFORM EXT-CHECK
           IF WS-FLAG = 'Y'
               PERFORM LOCAL-WORK
           END-IF
           GOBACK.
       LOCAL-WORK.
           DISPLAY 'WORK'.
       LOCAL-UNUSED.
           DISPLAY 'UNUSED'.
           COPY EXTPARA.
