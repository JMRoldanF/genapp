      * Procedure-division copybook: paragraphs CPYFLOW reaches.
       COPY-VALIDATE.
           IF WS-AMOUNT < 0
               PERFORM OWN-REJECT
           END-IF.
       COPY-TOTAL.
           ADD WS-AMOUNT TO WS-TOTAL.
