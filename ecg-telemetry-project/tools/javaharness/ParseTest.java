import com.example.ecg.net.FrameParser;
import java.io.*;
import java.util.*;
public class ParseTest {
    public static void main(String[] a) throws Exception {
        byte[] all = System.in.readAllBytes();
        int chunk = Integer.parseInt(a[0]);
        FrameParser p = new FrameParser();
        StringBuilder sb = new StringBuilder();
        final int[] drops = {0};
        FrameParser.Listener l = (s, seq, dropped) -> {
            drops[0]+=dropped;
            sb.append(seq).append(":");
            for (int i=0;i<s.length;i++) sb.append(Math.round(s[i]*1000)).append(i<s.length-1?",":"");
            sb.append("\n");
        };
        for (int i=0;i<all.length;i+=chunk) {
            int n=Math.min(chunk,all.length-i);
            byte[] b=Arrays.copyOfRange(all,i,i+n);
            p.feed(b,n,l);
        }
        System.out.print(sb);
        System.err.println("dropped="+drops[0]);
    }
}
