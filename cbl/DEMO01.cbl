       IDENTIFICATION DIVISION.                                         00000100
       PROGRAM-ID. DEMO01.                                              00000200
       ENVIRONMENT DIVISION.                                            00000300
       INPUT-OUTPUT SECTION.                                            00000400
       FILE-CONTROL.                                                    00000500
           SELECT IN-FILE ASSIGN TO DA-INFILE                           00000600
               ORGANIZATION IS INDEXED                                  00000700
               ACCESS MODE IS RANDOM                                    00000800
               RECORD KEY IS IN-KEY                                     00000900
               NOMINAL KEY IS WS-KEY.                                   00001000
           SELECT SEQ-FILE ASSIGN TO DA-SEQ                             00001100
               ACCESS MODE IS SEQUENTIAL                                00001200
               PROCESSING MODE IS SEQUENTIAL.                           00001300
           SELECT OUT-FILE ASSIGN TO DA-OUT,                            00001400
               ORGANIZATION IS SEQUENTIAL.                              00001500
       I-O-CONTROL.                                                     00001600
           APPLY CORE-INDEX ON IN-FILE.                                 00001700
       DATA DIVISION.                                                   00001800
       FILE SECTION.                                                    00001900
       FD  IN-FILE.                                                     00002000
       01  IN-REC.                                                      00002100
           05  IN-KEY          PIC X(8).                                00002200
           05  IN-DATA         PIC X(72).                               00002300
       FD  SEQ-FILE.                                                    00002400
       01  SEQ-REC             PIC X(80).                               00002500
       FD  OUT-FILE.                                                    00002600
       01  OUT-REC             PIC X(80).                               00002700
       WORKING-STORAGE SECTION.                                         00002800
       01  WS-FLAG             PIC X.                                   00002900
       01  WS-KEY              PIC X(8).                                00003000
       01  WS-LO               PIC 9(4) COMP.                           00003100
       01  WS-HI               PIC 9(4) COMP.                           00003200
       01  WS-DATE             PIC 9(6) DATE FORMAT YYXXXX.             00003300
       PROCEDURE DIVISION.                                              00003400
       MAIN-PARA.                                                       00003500
           MOVE 'Y' TO WS-FLAG                                          00003600
           ADD 1 TO WS-LO; ADD 1 TO WS-HI                               00003700
           PERFORM P100                                                 00003800
           GOBACK.                                                      00003900
       P100.                                                            00004000
           DISPLAY 'QA V1923 FLAG ' WS-FLAG.                            00004100
