import { describe, expect, it } from "vitest";
import { isEmail } from "./emails";

describe("адрес почты", () => {
  it("принимает обычные и внутренние адреса", () => {
    for (const a of ["ivan.petrov@example.org", "a+b@mail.example.org", " user@example.org ", "user@mailhost"]) expect(isEmail(a), a).toBe(true);
  });
  it("отклоняет то, что не примет сервер", () => {
    for (const a of ["", "user", "user@", "@example.org", "a b@example.org", "a@example.org,b@example.org", "имя@example.org"]) expect(isEmail(a), a).toBe(false);
  });
});
