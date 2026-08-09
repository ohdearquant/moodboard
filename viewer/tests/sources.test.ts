import { MAX_REPORT_BYTES } from "../src/limits";
import { LocalFileSource } from "../src/sources";

describe("bounded local report source", () => {
  it("refuses an oversized File before requesting its ArrayBuffer", async () => {
    const arrayBuffer = vi.fn<() => Promise<ArrayBuffer>>();
    const file = {
      name: "oversized.json",
      size: MAX_REPORT_BYTES + 1,
      arrayBuffer,
    } as unknown as File;

    const result = await new LocalFileSource(file).read();

    expect(result.ok).toBe(false);
    expect(arrayBuffer).not.toHaveBeenCalled();
    if (!result.ok) expect(result.issue.code).toBe("source-read");
  });
});
