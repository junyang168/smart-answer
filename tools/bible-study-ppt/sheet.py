"""Contact sheets: <qa dir>/img/sN.jpeg -> <qa dir>/sheetNN.jpg, N slides per sheet, numbered."""
import sys,glob,re
from PIL import Image,ImageDraw
d=sys.argv[1]; per=int(sys.argv[2]) if len(sys.argv)>2 else 6
fs=sorted(glob.glob(f"{d}/img/*.jpeg")+glob.glob(f"{d}/img/*.jpg"),key=lambda p:int(re.findall(r"(\d+)\.jpe?g$",p)[0]))
tw=800
for k in range(0,len(fs),per):
    ims=[Image.open(f) for f in fs[k:k+per]]
    th=int(ims[0].height*tw/ims[0].width)
    sheet=Image.new("RGB",(tw*2+30,(th+30)*((len(ims)+1)//2)),"#888")
    for i,im in enumerate(ims):
        x=(i%2)*(tw+30); y=(i//2)*(th+30)
        sheet.paste(im.resize((tw,th)),(x,y+25))
        ImageDraw.Draw(sheet).text((x+5,y+5),f"#{k+i+1}",fill="white")
    sheet.save(f"{d}/sheet{k//per+1:02d}.jpg",quality=80)
print(len(fs))
