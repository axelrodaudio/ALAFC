ALAFC - бърз старт (Windows)
============================

Файлове: alafc.py (кодекът), alafc_convert.py (конвертор),
alafc_player.py (CLI player), neoamp.py (плеър с интерфейс),
truescope.py + truescope_loader.py (честотен анализатор + плеър),
alafc_lossless_tester.py (детектор за fake-lossless),
alafc_gui_logic.py (споделена логика за NeoAmp/TrueScope),
LICENSE.txt (правата - MIT, твои са).

ВАРИАНТ А - готови .exe (без Python)
--------------------------------------
Свали ги от Releases страницата на GitHub, или си ги построй сам с
ALAFC_Build_EXE.bat (двоен клик, ~15 мин, строи всичките 6 инструмента).
После просто двоен клик / довлачи файлове върху тях.
Виж PACKAGING_NOTES.md за бележка относно фалшиви антивирусни тревоги.

ВАРИАНТ Б - от изходния код
------------------------------
1) Инсталация (веднъж, в терминал):
   pip install numpy sounddevice numba scipy matplotlib

   (numba не е задължителен, но прави encode/decode 30-60x по-бърз.
    За FLAC/MP3 вход: winget install Gyan.FFmpeg)

2) Конвертиране:
   python alafc_convert.py "песен.flac"
   python alafc_convert.py --folder "D:\Music\Album"
   (или довлачи файл върху ALAFC_Converter.bat)

3) Слушане - NeoAmp (графичен плеър, ASIO/WASAPI):
   python neoamp.py "песен.alafc"

4) Проверка за истински lossless - TrueScope (графика + плеър):
   python truescope.py
   (после "+ Добави" в прозореца и избери файл)

5) CLI плеър, ако предпочиташ команден ред:
   python alafc_player.py --list
   python alafc_player.py "песен.alafc" --device <номер>

6) Обратно към WAV:
   python alafc_convert.py "песен.alafc"

Всеки decode проверява вградения MD5 - ако пише
"verified lossless (MD5 OK)", звукът е бит по бит оригиналът.

От v4 нататък: L/R срещу mid/side се избира отделно за всеки ~6-сек
сегмент, не веднъж за цялата песен - файлове, чийто стерео характер
се променя (тих mono началото, широк chorus), се компресират по-добре.
