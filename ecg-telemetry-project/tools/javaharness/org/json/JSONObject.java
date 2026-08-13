package org.json;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * TEST-ONLY minimal JSON reader.
 *
 * Android ships a real org.json; a desktop JDK does not. Earlier this was an
 * empty stub that only satisfied the compiler, which meant the tree evaluator
 * in BeatClassifier could be compiled but never exercised outside Xcode-style
 * tooling. Since the tree is now the production classifier, cross_check.py must
 * actually run it -- so this parses enough JSON to load model.json for real:
 * objects, arrays, numbers, strings, booleans and null.
 */
public class JSONObject {

    private final Map<String, Object> map = new LinkedHashMap<>();

    public JSONObject(String src) throws Exception {
        Parser p = new Parser(src);
        Object v = p.parseValue();
        if (!(v instanceof JSONObject)) throw new Exception("not a JSON object");
        this.map.putAll(((JSONObject) v).map);
    }

    JSONObject() { }

    void put(String k, Object v) { map.put(k, v); }

    public JSONArray getJSONArray(String k) throws Exception {
        Object v = map.get(k);
        if (!(v instanceof JSONArray)) throw new Exception("not an array: " + k);
        return (JSONArray) v;
    }

    public String optString(String k, String dflt) {
        Object v = map.get(k);
        return (v instanceof String) ? (String) v : dflt;
    }

    // ---- parser ----------------------------------------------------------
    static class Parser {
        private final String s;
        private int i = 0;
        Parser(String s) { this.s = s; }

        void ws() { while (i < s.length() && Character.isWhitespace(s.charAt(i))) i++; }

        Object parseValue() throws Exception {
            ws();
            char c = s.charAt(i);
            if (c == '{') return parseObject();
            if (c == '[') return parseArray();
            if (c == '"') return parseString();
            if (s.startsWith("true", i))  { i += 4; return Boolean.TRUE; }
            if (s.startsWith("false", i)) { i += 5; return Boolean.FALSE; }
            if (s.startsWith("null", i))  { i += 4; return null; }
            return parseNumber();
        }

        JSONObject parseObject() throws Exception {
            JSONObject o = new JSONObject();
            i++;                                   // consume '{'
            ws();
            if (s.charAt(i) == '}') { i++; return o; }
            while (true) {
                ws();
                String key = parseString();
                ws();
                if (s.charAt(i) != ':') throw new Exception("expected ':'");
                i++;
                o.put(key, parseValue());
                ws();
                char c = s.charAt(i++);
                if (c == '}') return o;
                if (c != ',') throw new Exception("expected ',' or '}'");
            }
        }

        JSONArray parseArray() throws Exception {
            JSONArray a = new JSONArray();
            i++;                                   // consume '['
            ws();
            if (s.charAt(i) == ']') { i++; return a; }
            while (true) {
                a.add(parseValue());
                ws();
                char c = s.charAt(i++);
                if (c == ']') return a;
                if (c != ',') throw new Exception("expected ',' or ']'");
            }
        }

        String parseString() throws Exception {
            if (s.charAt(i) != '"') throw new Exception("expected string");
            i++;
            StringBuilder sb = new StringBuilder();
            while (s.charAt(i) != '"') {
                char c = s.charAt(i++);
                if (c == '\\') {
                    char e = s.charAt(i++);
                    switch (e) {
                        case 'n': sb.append('\n'); break;
                        case 't': sb.append('\t'); break;
                        case 'r': sb.append('\r'); break;
                        case 'b': sb.append('\b'); break;
                        case 'f': sb.append('\f'); break;
                        case 'u': sb.append((char) Integer.parseInt(s.substring(i, i + 4), 16)); i += 4; break;
                        default:  sb.append(e);
                    }
                } else {
                    sb.append(c);
                }
            }
            i++;
            return sb.toString();
        }

        Double parseNumber() {
            int start = i;
            while (i < s.length() && "+-0123456789.eE".indexOf(s.charAt(i)) >= 0) i++;
            return Double.valueOf(s.substring(start, i));
        }
    }
}
