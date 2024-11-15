
import os
import vlc

os.environ['DISPLAY'] = ':0'

player = vlc.MediaPlayer("gms_k1.mp3")
player.play()
