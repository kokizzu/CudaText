(*
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at http://mozilla.org/MPL/2.0/.

Copyright (c) Alexey Torgashin
*)
unit form_lexer_lib;

{$mode objfpc}{$H+}

interface

uses
  Classes, SysUtils, Forms, Controls, Graphics, Dialogs, ButtonPanel,
  StdCtrls, ComCtrls, IniFiles,
  LCLIntf, LCLType, LCLProc, ExtCtrls,
  LazUTF8, LazFileUtils,
  ATStringProc,
  ec_SyntAnal,
  form_lexer_prop,
  proc_globdata,
  proc_customdialog,
  proc_miscutils,
  proc_msg,
  math;

type
  { TfmLexerLib }

  TfmLexerLib = class(TForm)
    btnConfig: TButton;
    btnDelete: TButton;
    List: TListBox;
    PanelBtn: TButtonPanel;
    PanelTop: TPanel;
    procedure btnDeleteClick(Sender: TObject);
    procedure btnConfigClick(Sender: TObject);
    procedure FormClose(Sender: TObject; var CloseAction: TCloseAction);
    procedure FormCreate(Sender: TObject);
    procedure FormKeyDown(Sender: TObject; var Key: Word; Shift: TShiftState);
    procedure FormShow(Sender: TObject);
  private
    { private declarations }
    FOnDeleteLexer: TAppStringEvent;
    procedure Localize;
    procedure UpdateList;
  public
    { public declarations }
    FontName: string;
    FontSize: integer;
    DirAcp: string;
    property OnDeleteLexer: TAppStringEvent read FOnDeleteLexer write FOnDeleteLexer;
  end;


function DoDialogLexerLibraryEx(
  const ADirAcp: string;
  const AFontName: string;
  AFontSize: integer;
  AOnDeleteLexer: TAppStringEvent): boolean;

implementation

{$R *.lfm}

const
  msgLexerHiddenSuffix: string = '(hidden)';
  msgLexerLinks: string = 'links:';


function DoDialogLexerLibraryEx(const ADirAcp: string; const AFontName: string;
  AFontSize: integer; AOnDeleteLexer: TAppStringEvent): boolean;
var
  F: TfmLexerLib;
begin
  F:= TfmLexerLib.Create(nil);
  try
    F.OnDeleteLexer:= AOnDeleteLexer;
    F.FontName:= AFontName;
    F.FontSize:= AFontSize;
    F.DirAcp:= ADirAcp;
    F.ShowModal;
    Result:= AppManager.Modified;
  finally
    F.Free;
  end;
end;

function IsLexerLinkDup(an: TecSyntAnalyzer; LinkN: integer): boolean;
var
  i: integer;
begin
  Result:= false;
  for i:= 0 to LinkN-1 do
    if an.SubAnalyzers[i].SyntAnalyzer=an.SubAnalyzers[LinkN].SyntAnalyzer then
    begin
      Result:= true;
      exit
    end;
end;


procedure SEscapeSpecialChars(var S: string);
var
  i: byte;
begin
  SReplaceAll(S, ' ', '_');

  for i in [ord('#'), ord('*'), ord('|'), ord('/')] do
    SReplaceAll(S, Chr(i), '%'+IntToHex(i, 2));
end;


procedure DeletePackagesIniSection(ALexerName: string);
var
  fn: string;
  Ini: TIniFile;
begin
  SEscapeSpecialChars(ALexerName);

  fn:= AppDir_Settings+DirectorySeparator+'packages.ini';
  if FileExists(fn) then
  begin
    Ini:= TIniFile.Create(fn);
    try
      Ini.EraseSection('lexer.'+ALexerName+'.zip');
    finally
      FreeAndNil(Ini);
    end;
  end;
end;

{ TfmLexerLib }

procedure TfmLexerLib.Localize;
const
  section = 'd_lex_lib';
var
  ini: TIniFile;
  fn: string;
begin
  fn:= AppFile_Language;
  if not FileExists(fn) then exit;
  ini:= TIniFile.Create(fn);
  try
    Caption:= ini.ReadString(section, '_', Caption);
    with PanelBtn.CloseButton do Caption:= msgButtonClose;
    with btnConfig do Caption:= ini.ReadString(section, 'cfg', Caption);
    with btnDelete do Caption:= ini.ReadString(section, 'del', Caption);
    //with F.btnShowHide do Caption:= ini.ReadString(section, 'hid', Caption);
    msgLexerHiddenSuffix:= ini.ReadString(section, 'hidmk', msgLexerHiddenSuffix);
    msgLexerLinks:= ini.ReadString(section, 'lns', msgLexerLinks);
  finally
    FreeAndNil(ini);
  end;
end;


procedure TfmLexerLib.FormShow(Sender: TObject);
begin
  UpdateFormOnTop(Self);
  UpdateList;
  if List.Items.Count>0 then
    List.ItemIndex:= 0;
end;


procedure TfmLexerLib.btnConfigClick(Sender: TObject);
var
  an: TecSyntAnalyzer;
  n: integer;
begin
  List.SetFocus;

  n:= List.ItemIndex;
  if n<0 then exit;
  an:= List.Items.Objects[n] as TecSyntAnalyzer;

  if DoDialogLexerPropEx(an, FontName, FontSize) then
  begin
    //DoLexerExportFromLibToFile(an);
    UpdateList;
    List.ItemIndex:= n;
  end;
end;

procedure TfmLexerLib.FormClose(Sender: TObject; var CloseAction: TCloseAction);
begin
  FormHistorySave(Self, '/pos/lexerlib', false);
end;

procedure TfmLexerLib.FormCreate(Sender: TObject);
begin
  Localize;
  DoForm_ScaleAuto(Self, false);

  FormHistoryLoad(Self, '/pos/lexerlib', false, Screen.DesktopRect);
end;

procedure TfmLexerLib.FormKeyDown(Sender: TObject; var Key: Word;
  Shift: TShiftState);
begin
  if (Key=VK_DELETE) and (Shift=[]) then
  begin
    btnDelete.Click;
    Key:= 0;
    exit
  end;

  if (Key=VK_RETURN) and (Shift=[]) then
  begin
    btnConfig.Click;
    Key:= 0;
    exit
  end;

  if (Key=VK_ESCAPE) and (Shift=[]) then
  begin
    Close;
    Key:= 0;
    exit
  end;
end;

procedure TfmLexerLib.btnDeleteClick(Sender: TObject);
var
  An: TecSyntAnalyzer;
  NIndex: integer;
  SLexerName: string;
begin
  List.SetFocus;

  NIndex:= List.ItemIndex;
  if (NIndex<0) or (NIndex>=List.Count) then exit;
  An:= List.Items.Objects[NIndex] as TecSyntAnalyzer;
  SLexerName:= An.LexerName;

  if MsgBox(
    Format(msgConfirmDeleteLexer, [SLexerName]),
    MB_OKCANCEL or MB_ICONWARNING)=ID_OK then
  begin
    if Assigned(FOnDeleteLexer) then
      FOnDeleteLexer(nil, SLexerName);

    DeleteFile(AppFile_Lexer(SLexerName));
    DeleteFile(AppFile_LexerMap(SLexerName));
    DeleteFile(AppFile_LexerAcp(SLexerName));
    DeleteFile(AppFile_LexerSpecificConfig(SLexerName, true));
    DeletePackagesIniSection(SLexerName);

    AppManager.DeleteLexer(An);
    AppManager.Modified:= true;

    UpdateList;
    List.ItemIndex:= Min(NIndex, List.Count-1);
  end;
end;

procedure TfmLexerLib.UpdateList;
const
  cPadding = 4; //pixels to add to ScrollWidth
var
  sl: TStringList;
  an: TecSyntAnalyzer;
  an_sub: TecSubAnalyzerRule;
  links, suffix, SListItem: string;
  NPrevIndex, NScrollWidth, i, j: integer;
begin
  NPrevIndex:= List.ItemIndex;
  NScrollWidth:= 100;

  sl:= TStringList.Create;
  try
    List.Items.BeginUpdate;
    List.Items.Clear;
    List.Canvas.Font.Assign(Self.Font);

    for i:= 0 to AppManager.LexerCount-1 do
    begin
      an:= AppManager.Lexers[i];
      if an.Deleted then Continue;
      sl.AddObject(an.LexerName, an);
    end;
    sl.Sort;

    for i:= 0 to sl.Count-1 do
    begin
      an:= sl.Objects[i] as TecSyntAnalyzer;

      links:= '';
      for j:= 0 to an.SubAnalyzers.Count-1 do
        if not IsLexerLinkDup(an, j) then
        begin
          if links='' then
            links:= msgLexerLinks+' '
          else
            links:= links+', ';
          an_sub:= an.SubAnalyzers[j];
          if an_sub<>nil then
            if an_sub.SyntAnalyzer<>nil then
              links:= links+an_sub.SyntAnalyzer.LexerName
            else
              links:= links+'?';
        end;
      if links<>'' then
        links:= '  ('+links+')';

      suffix:= '';
      if an.Internal then
        suffix:= '    '+msgLexerHiddenSuffix;

      SListItem:= sl[i] + links + suffix;
      List.Items.AddObject(SListItem, an);

      NScrollWidth:= Max(NScrollWidth, List.Canvas.TextWidth(SListItem));
    end;

    List.ScrollWidth:= NScrollWidth+cPadding;
  finally
    List.Items.EndUpdate;
    FreeAndNil(sl);
  end;

  if (NPrevIndex>=0) and (NPrevIndex<List.Count) then
    List.ItemIndex:= NPrevIndex;
end;

end.
