package org.json;

import java.util.ArrayList;
import java.util.List;

/** TEST-ONLY companion to the minimal JSONObject reader. */
public class JSONArray {

    private final List<Object> items = new ArrayList<>();

    void add(Object o) { items.add(o); }

    public int length() { return items.size(); }

    public JSONArray getJSONArray(int i) throws Exception {
        Object v = items.get(i);
        if (!(v instanceof JSONArray)) throw new Exception("not an array at " + i);
        return (JSONArray) v;
    }

    public double optDouble(int i, double dflt) {
        Object v = items.get(i);
        return (v instanceof Double) ? (Double) v : dflt;
    }

    public int optInt(int i, int dflt) {
        Object v = items.get(i);
        return (v instanceof Double) ? (int) Math.round((Double) v) : dflt;
    }
}
