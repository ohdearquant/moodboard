import { flattenMeasurement } from "../src/format";

it("flattens nested abstention measurements with typed values and stable pointer order", () => {
  expect(
    flattenMeasurement({ z: null, a: { items: [true, { n: 0.1000001 }], empty: [] } }),
  ).toEqual([
    { path: "/a/empty", value: "[]" },
    { path: "/a/items/0", value: "true" },
    { path: "/a/items/1/n", value: "0.1000001" },
    { path: "/z", value: "null" },
  ]);
});
