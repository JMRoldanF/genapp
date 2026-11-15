       IDENTIFICATION DIVISION.                                         00000100
       PROGRAM-ID. CBPOSTST.                                            00000200
       ENVIRONMENT DIVISION.                                            00000300
       INPUT-OUTPUT SECTION.                                            00000400
       FILE-CONTROL.                                                    00000500
           SELECT TRAN-FILE ASSIGN TO TRANFILE                          00000600
                  ORGANIZATION IS SEQUENTIAL.                           00000700
           SELECT ACCT-FILE ASSIGN TO ACCTFILE                          00000800
                  ORGANIZATION IS INDEXED                               00000900
                  ACCESS MODE  IS RANDOM                                00001000
                  RECORD KEY   IS ACCT-ID.                              00001100
           SELECT IDLE-FILE ASSIGN TO IDLEFILE                          00001200
                  ORGANIZATION IS SEQUENTIAL.                           00001300
       DATA DIVISION.                                                   00001400
       FILE SECTION.                                                    00001500
       FD  TRAN-FILE.                                                   00001600
       01  TRAN-RECORD                 PIC X(100).                      00001700
       FD  ACCT-FILE.                                                   00001800
       01  ACCT-RECORD.                                                 00001900
           05  ACCT-ID                 PIC X(11).                       00002000
           05  ACCT-BALANCE            PIC S9(10)V99.                   00002100
       FD  IDLE-FILE.                                                   00002200
       01  IDLE-RECORD                 PIC X(80).                       00002300
       PROCEDURE DIVISION.                                              00002400
       MAIN-PARA.                                                       00002500
           OPEN OUTPUT TRAN-FILE                                        00002600
           WRITE TRAN-RECORD                                            00002700
           CLOSE TRAN-FILE                                              00002800
           OPEN I-O ACCT-FILE                                           00002900
           READ ACCT-FILE                                               00003000
           REWRITE ACCT-RECORD                                          00003100
           CLOSE ACCT-FILE                                              00003200
           OPEN INPUT IDLE-FILE                                         00003300
           CLOSE IDLE-FILE                                              00003400
           GOBACK.                                                      00003500
