(*
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at http://mozilla.org/MPL/2.0/.

Copyright (c) Alexey Torgashin
*)
unit TreeHelper_Markdown;

{$mode objfpc}{$H+}

interface

uses
  SysUtils,
  ATStrings,
  ATSynEdit,
  TreeHelpers_Base;

type
  TTreeHelperMarkdown = class
  private
    class function GetHeadLevel(const S: UnicodeString): integer;
    class function IsFencedBlock(const S: UnicodeString): boolean;
    class function TrimHead(const S: UnicodeString): UnicodeString;
  public
    class procedure GetHeaders(Ed: TATSynEdit; Data: TATTreeHelperRecords);
  end;

implementation

class function TTreeHelperMarkdown.GetHeadLevel(const S: UnicodeString): integer;
var
  r, NLen: integer;
begin
  Result:= 0;
  if S[1]<>'#' then exit;
  r:= 0;
  NLen:= Length(S);
  while (r<NLen) and (S[r+1]='#') do
    Inc(r);
  if (r<NLen) and (r<=6) and (S[r+1]=' ') then
    Result:= r;
end;

class function TTreeHelperMarkdown.IsFencedBlock(const S: UnicodeString): boolean;
//regex '^\s*`{3,}\s*\w*$'
var
  NLen, i, r: integer;
begin
  Result:= false;
  NLen:= Length(S);
  if NLen=0 then exit;
  i:= 0;
  while (i<NLen) and ((S[i+1]=' ') or (S[i+1]=#9)) do
    Inc(i);
  r:= 0;
  while (i<NLen) and (S[i+1]='`') do
  begin
    Inc(r);
    Inc(i);
  end;
  Result:= r>=3;
end;

class function TTreeHelperMarkdown.TrimHead(const S: UnicodeString): UnicodeString;
var
  i: integer;
begin
  i:= 1;
  while (i<=Length(S)) and ((S[i]='#') or (S[i]=' ')) do
    Inc(i);
  Result:= Copy(S, i);
end;

class procedure TTreeHelperMarkdown.GetHeaders(Ed: TATSynEdit; Data: TATTreeHelperRecords);
var
  PrevHeadIndex: array[1..6] of integer = (-1, -1, -1, -1, -1, -1);
  //
  procedure ClosePrevHeader(head, iLine: integer);
  var
    ItemPtr: PATTreeHelperRecord;
    iHead: integer;
  begin
    for iHead:= head to High(PrevHeadIndex) do
      if PrevHeadIndex[iHead]>=0 then
      begin
        ItemPtr:= Data._GetItemPtr(PrevHeadIndex[iHead]);
        if ItemPtr^.Y2<0 then
          ItemPtr^.Y2:= iLine-1
      end;

    if (head>=Low(PrevHeadIndex)) and (head<=High(PrevHeadIndex)) then
      PrevHeadIndex[head]:= Data.Count-1;
  end;
  //
  function CheckUnderline(St: TATStrings; ALine: integer; AChar: WideChar; ALevel: integer): integer;
  var
    i: integer;
  begin
    for i:= 1 to St.LinesLen[ALine] do
      if St.LineCharAt(ALine, i)<>AChar then
        exit(0);
    Result:= ALevel;
  end;
  //
var
  DataItem: TATTreeHelperRecord;
  St: TATStrings;
  bFencedEntered, bFencedPrev, bFencedCurrent: boolean;
  bPreformatted: boolean;
  HeadLevel, HeadLevelUnderlined: integer;
  S, S2: UnicodeString;
  NLineCount, NLen, iLine, iChar: integer;
  ch: WideChar;
begin
  Data.Clear;
  bFencedEntered:= false;
  bFencedPrev:= false;
  bFencedCurrent:= false;
  bPreformatted:= false;
  St:= Ed.Strings;
  NLineCount:= St.Count;

  for iLine:= 0 to NLineCount-1 do
  begin
    NLen:= St.LinesLen[iLine];

    iChar:= 0;
    while (iChar<NLen) and (St.LineCharAt(iLine, iChar+1)=' ') do
      Inc(iChar);
    if (iChar+2>NLen) then Continue;

    ch:= St.LineCharAt(iLine, iChar+1);

    HeadLevelUnderlined:= 0;
    if iLine+1<NLineCount then
      case St.LineCharAt(iLine+1, 1) of
        '=':
          HeadLevelUnderlined:= CheckUnderline(St, iLine+1, '=', 1);
        '-':
          HeadLevelUnderlined:= CheckUnderline(St, iLine+1, '-', 2);
      end;

    if (ch<>'<') and (ch<>'#') and (HeadLevelUnderlined=0) then Continue;
    S:= St.Lines[iLine];

    if ch='<' then
      case Trim(S) of
        '<pre>':
          begin
            bPreformatted:= true;
            Continue;
          end;
        '</pre>':
          begin
            bPreformatted:= false;
            Continue;
          end;
      end;
    if bPreformatted then
      Continue;

    bFencedCurrent:= IsFencedBlock(S);
    if bFencedCurrent then
    begin
      if bFencedEntered and (bFencedCurrent=bFencedPrev) then
      begin
        bFencedEntered:= false;
        bFencedPrev:= false;
      end
      else
      begin
        bFencedEntered:= true;
        bFencedPrev:= bFencedCurrent;
      end;
      Continue;
    end;
    if bFencedEntered then
      Continue;

    HeadLevel:= GetHeadLevel(S);
    if HeadLevel>0 then
    begin
      DataItem.X1:= 0;
      DataItem.Y1:= iLine;
      DataItem.X2:= 0;
      DataItem.Y2:= -1;
      DataItem.Level:= HeadLevel;
      DataItem.Title:= TrimHead(S);
      DataItem.Icon:= -1;
      Data.Add(DataItem);
      ClosePrevHeader(HeadLevel, iLine);
    end
    else
    if HeadLevelUnderlined>0 then
    begin
      DataItem.X1:= 0;
      DataItem.Y1:= iLine;
      DataItem.X2:= 0;
      DataItem.Y2:= -1;
      DataItem.Level:= HeadLevelUnderlined;
      DataItem.Title:= Trim(S);
      DataItem.Icon:= -1;
      Data.Add(DataItem);
      ClosePrevHeader(HeadLevelUnderlined, iLine);
    end;
  end;

  ClosePrevHeader(Low(PrevHeadIndex), St.Count-1);
end;

end.

