$ErrorActionPreference = 'Stop'
Add-Type -ReferencedAssemblies System.Drawing -TypeDefinition @"
using System;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.Runtime.InteropServices;
public static class HarnessScreen {
    [DllImport("user32.dll")] static extern bool SetProcessDpiAwarenessContext(IntPtr value);
    public static void Run() {
        SetProcessDpiAwarenessContext(new IntPtr(-4));
        // Prepare drawing and transport without reading any screen pixels.
        using(var warm=new Bitmap(1,1,PixelFormat.Format24bppRgb)) using(var stream=new MemoryStream()) {
            using(var graphics=Graphics.FromImage(warm)) graphics.Clear(Color.Black);
            warm.Save(stream,ImageFormat.Bmp);
        }
        var output=Console.OpenStandardOutput();
        Console.Out.WriteLine("ready");string line;
        while((line=Console.In.ReadLine())!=null) {
            try {
                string[] parts=line.Split(',');
                if(parts.Length!=4) throw new ArgumentException();
                int x=int.Parse(parts[0]),y=int.Parse(parts[1]),w=int.Parse(parts[2]),h=int.Parse(parts[3]);
                if(w<1||h<1||(long)w*h>100000000) throw new ArgumentException();
                using(var image=new Bitmap(w,h,PixelFormat.Format24bppRgb)) using(var stream=new MemoryStream()) {
                    using(var graphics=Graphics.FromImage(image)) graphics.CopyFromScreen(x,y,0,0,image.Size,CopyPixelOperation.SourceCopy);
                    image.Save(stream,ImageFormat.Bmp);byte[] bytes=stream.ToArray();
                    byte[] header=System.Text.Encoding.ASCII.GetBytes(bytes.Length+"\n");output.Write(header,0,header.Length);output.Write(bytes,0,bytes.Length);output.Flush();
                }
            } catch {Console.Out.WriteLine("error");}
        }
    }
}
"@
[HarnessScreen]::Run()
