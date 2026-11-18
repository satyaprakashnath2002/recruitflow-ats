import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { QuickLink } from "./dashboard-page";

describe("dashboard quick links", () => {
  it("renders workflow actions as a structured, accessible link", () => {
    const markup = renderToStaticMarkup(
      <QuickLink href="/applications" icon="file" title="Review applications" detail="Continue candidate review within your access" />,
    );

    expect(markup).toContain('class="quick-link"');
    expect(markup).toContain('href="/applications"');
    expect(markup).toContain("Review applications");
    expect(markup).toContain("Continue candidate review within your access");
    expect(markup).toContain('class="quick-icon"');
  });
});
