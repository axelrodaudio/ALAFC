ALAFC 1.0 - бърз старт
======================

WINDOWS (папка C:\ALAFC, веднъж: pip install numpy sounddevice numba)
  py alafc_convert.py "песен.flac"            -> песен.alafc
  py alafc_convert.py --folder "D:\Music\Album"
  py alafc_player.py --list                    (намери ASIO номера)
  py alafc_player.py "песен.alafc" --device 10
  py alafc_convert.py "песен.alafc"            -> обратно WAV
  py alafc.py info "песен.alafc"               (какво е файлът)

ANDROID / всичко друго
  Отвори alafc_player_android.html с Chrome, избери .alafc файл, Пусни.
  (16-bit файлове; hi-res 24/32-bit - на компютъра)

КАКВО ЗНАЧАТ НАДПИСИТЕ
  verified lossless (MD5 OK) - битовете са точно оригиналът
  RECOVERED: N damaged segment(s) - файлът е повреден, но оцеля:
     заглушен е само раненият ~6-сек сегмент, казва ти къде е

ДЕМОТА (в папка demos)
  demo_music.alafc                  обикновено 16/44.1
  demo_hires_stereo_24_192.alafc    24-bit/192kHz, истинско стерео
  demo_damaged_survives.alafc       нарочно счупен - чуй как оцелява
  stereo_test.alafc                 3 бипа ляво, 3 дясно, двете заедно

Правата: LICENSE.txt - MIT, (c) 2026 Axelrod. Кодекът е твой.
