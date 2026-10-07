import { describe, expect, it } from "vitest";
import { isValidLogin } from "./loginRules";

describe("формат логина", () => {
  it.each(["ivanov", "i.ivanov", "ivan_petrov-2", "иванов.и", "CORP\\ivanov", "ivanov@corp.local", " ivanov "])("принимает %s", (v) => expect(isValidLogin(v)).toBe(true));
  it.each(["", "a b", "a'b", 'a"b', "a)(uid=*", "*", "a;b", "a|b", "a=b", "a/b", "a\\", "@corp.local", "a@b@c", ".hidden", "a​b", "a‮b", "a\nb", "x".repeat(300)])(
    "отклоняет %j", (v) => expect(isValidLogin(v)).toBe(false));
});
