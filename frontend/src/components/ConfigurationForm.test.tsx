import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import type { FormPrefill } from "../derive/prefill";
import { server } from "../test/server";
import { ConfigurationForm } from "./ConfigurationForm";

const START = "http://localhost/api/agent/start";

async function fillMinimum(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText(/repository url/i), "https://example.invalid/r.git");
  await user.type(screen.getByLabelText(/dependency/i), "pydantic");
  await user.type(screen.getByLabelText(/current version/i), "1.10.13");
  await user.type(screen.getByLabelText(/target version/i), "2.9.2");
}

describe("ConfigurationForm", () => {
  it("offers the four constraint fields the backend models", () => {
    render(<ConfigurationForm onStarted={() => {}} />);

    expect(screen.getByLabelText(/zero downtime/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/minimize effort/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/deadline/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/risk tolerance/i)).toBeInTheDocument();
  });

  it("offers no field the backend has nowhere to put", () => {
    // READINESS 2.5, 2.10: model and temperature are environment variables and
    // there is no configuration endpoint; "Additional Context" is unbacked
    // prose entering the judgment path.
    render(<ConfigurationForm onStarted={() => {}} />);

    expect(screen.queryByLabelText(/temperature/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/model/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/additional context/i)).not.toBeInTheDocument();
  });

  it("takes the dependency as free text, not a dropdown", () => {
    // READINESS 2.6: `DependencySpec.name` is free text and nothing enumerates
    // a repository's manifest before a run starts.
    render(<ConfigurationForm onStarted={() => {}} />);

    expect(screen.getByLabelText(/dependency/i).tagName).toBe("INPUT");
  });

  it("refuses two version fields that are equal, before asking the server", async () => {
    // `DependencySpec` rejects this with its own validator. Mirroring it here
    // saves a round trip; the backend remains the authority.
    const user = userEvent.setup();
    render(<ConfigurationForm onStarted={() => {}} />);

    await user.type(screen.getByLabelText(/repository url/i), "https://example.invalid/r.git");
    await user.type(screen.getByLabelText(/dependency/i), "pydantic");
    await user.type(screen.getByLabelText(/current version/i), "2.9.2");
    await user.type(screen.getByLabelText(/target version/i), "2.9.2");
    await user.click(screen.getByRole("button", { name: /start/i }));

    expect(screen.getByText(/must differ/i)).toBeInTheDocument();
  });

  it("marks both version fields invalid when they must differ, not just one", async () => {
    // The relationship error implicates `version-from` and `version-to`
    // equally. A screen-reader user tabbed to either one needs to hear it;
    // the message itself stays rendered exactly once so `getByText` above
    // stays unambiguous.
    const user = userEvent.setup();
    render(<ConfigurationForm onStarted={() => {}} />);

    await user.type(screen.getByLabelText(/repository url/i), "https://example.invalid/r.git");
    await user.type(screen.getByLabelText(/dependency/i), "pydantic");
    await user.type(screen.getByLabelText(/current version/i), "2.9.2");
    await user.type(screen.getByLabelText(/target version/i), "2.9.2");
    await user.click(screen.getByRole("button", { name: /start/i }));

    const current = screen.getByLabelText(/current version/i);
    const target = screen.getByLabelText(/target version/i);

    expect(current).toHaveAttribute("aria-invalid", "true");
    expect(target).toHaveAttribute("aria-invalid", "true");
    expect(current).toHaveAccessibleDescription(/must differ/i);
    expect(target).toHaveAccessibleDescription(/must differ/i);
  });

  it("sends exactly one of url or path", async () => {
    // Spec 9.1: a request naming both is refused rather than resolved by
    // precedence, because quietly preferring one analyses a repository the
    // caller did not name and every citation then points at the wrong tree.
    const user = userEvent.setup();
    let body: { repo: { url: string | null; path: string | null } } | null = null;
    server.use(
      http.post(START, async ({ request }) => {
        body = (await request.json()) as typeof body;
        return HttpResponse.json(
          { thread_id: "t-7", status: "queued", poll_url: "/api/agent/status/t-7" },
          { status: 202 },
        );
      }),
    );
    render(<ConfigurationForm onStarted={() => {}} />);

    await fillMinimum(user);
    await user.click(screen.getByRole("button", { name: /start/i }));

    await waitFor(() => expect(body).not.toBeNull());
    expect(body!.repo.url).toBe("https://example.invalid/r.git");
    expect(body!.repo.path).toBeNull();
  });

  it("sends a local path when the local source is chosen", async () => {
    const user = userEvent.setup();
    let body: { repo: { url: string | null; path: string | null } } | null = null;
    server.use(
      http.post(START, async ({ request }) => {
        body = (await request.json()) as typeof body;
        return HttpResponse.json(
          { thread_id: "t-8", status: "queued", poll_url: "/api/agent/status/t-8" },
          { status: 202 },
        );
      }),
    );
    render(<ConfigurationForm onStarted={() => {}} />);

    await user.click(screen.getByRole("radio", { name: /local/i }));
    await user.type(screen.getByLabelText(/local path/i), "/srv/repo");
    await user.type(screen.getByLabelText(/dependency/i), "pydantic");
    await user.type(screen.getByLabelText(/current version/i), "1.10.13");
    await user.type(screen.getByLabelText(/target version/i), "2.9.2");
    await user.click(screen.getByRole("button", { name: /start/i }));

    await waitFor(() => expect(body).not.toBeNull());
    expect(body!.repo.path).toBe("/srv/repo");
    expect(body!.repo.url).toBeNull();
  });

  it("sends the constraints as the backend models them", async () => {
    const user = userEvent.setup();
    let body: { constraints: Record<string, unknown> } | null = null;
    server.use(
      http.post(START, async ({ request }) => {
        body = (await request.json()) as typeof body;
        return HttpResponse.json(
          { thread_id: "t-9", status: "queued", poll_url: "/api/agent/status/t-9" },
          { status: 202 },
        );
      }),
    );
    render(<ConfigurationForm onStarted={() => {}} />);

    await fillMinimum(user);
    await user.click(screen.getByLabelText(/zero downtime/i));
    await user.type(screen.getByLabelText(/deadline/i), "2026-09-30");
    await user.selectOptions(screen.getByLabelText(/risk tolerance/i), "low");
    await user.click(screen.getByRole("button", { name: /start/i }));

    await waitFor(() => expect(body).not.toBeNull());
    expect(body!.constraints).toEqual({
      zero_downtime: true,
      minimize_effort: false,
      deadline: "2026-09-30",
      risk_tolerance: "low",
    });
  });

  it("sends a null deadline rather than an empty string", async () => {
    // `deadline: date | None`. An empty string is a 422 the user cannot act on.
    const user = userEvent.setup();
    let body: { constraints: { deadline: string | null } } | null = null;
    server.use(
      http.post(START, async ({ request }) => {
        body = (await request.json()) as typeof body;
        return HttpResponse.json(
          { thread_id: "t-10", status: "queued", poll_url: "/api/agent/status/t-10" },
          { status: 202 },
        );
      }),
    );
    render(<ConfigurationForm onStarted={() => {}} />);

    await fillMinimum(user);
    await user.click(screen.getByRole("button", { name: /start/i }));

    await waitFor(() => expect(body).not.toBeNull());
    expect(body!.constraints.deadline).toBeNull();
  });

  it("renders a 422 against the field its code names", async () => {
    const user = userEvent.setup();
    server.use(
      http.post(START, () =>
        HttpResponse.json(
          { error: { code: "invalid_repo_url", message: "Only https and git URLs are accepted.", retryable: false, node: null } },
          { status: 422 },
        ),
      ),
    );
    render(<ConfigurationForm onStarted={() => {}} />);

    await fillMinimum(user);
    await user.click(screen.getByRole("button", { name: /start/i }));

    const field = screen.getByLabelText(/repository url/i);
    await waitFor(() => expect(field).toHaveAccessibleDescription(/only https and git urls/i));
    expect(field).toHaveAttribute("aria-invalid", "true");
  });

  it("falls back to a banner for a code that names no field", async () => {
    const user = userEvent.setup();
    server.use(
      http.post(START, () =>
        HttpResponse.json(
          { error: { code: "kb_unavailable", message: "The knowledge base is unavailable.", retryable: true, node: null } },
          { status: 503 },
        ),
      ),
    );
    render(<ConfigurationForm onStarted={() => {}} />);

    await fillMinimum(user);
    await user.click(screen.getByRole("button", { name: /start/i }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(/knowledge base is unavailable/i),
    );
  });

  it("re-enables the button after a refusal so the user can correct and retry", async () => {
    const user = userEvent.setup();
    server.use(
      http.post(START, () =>
        HttpResponse.json(
          { error: { code: "invalid_repo_url", message: "Only https and git URLs are accepted.", retryable: false, node: null } },
          { status: 422 },
        ),
      ),
    );
    render(<ConfigurationForm onStarted={() => {}} />);

    await fillMinimum(user);
    await user.click(screen.getByRole("button", { name: /start/i }));

    await waitFor(() => expect(screen.getByRole("button", { name: /start/i })).toBeEnabled());
  });

  it("hands the started run up with its thread id", async () => {
    const user = userEvent.setup();
    const onStarted = vi.fn();
    server.use(
      http.post(START, () =>
        HttpResponse.json(
          { thread_id: "t-42", status: "queued", poll_url: "/api/agent/status/t-42" },
          { status: 202 },
        ),
      ),
    );
    render(<ConfigurationForm onStarted={onStarted} />);

    await fillMinimum(user);
    await user.click(screen.getByRole("button", { name: /start/i }));

    await waitFor(() =>
      expect(onStarted).toHaveBeenCalledWith({
        threadId: "t-42",
        dependency: "pydantic",
        from: "1.10.13",
        to: "2.9.2",
      }),
    );
  });
  it("starts empty when given no prefill", () => {
    render(<ConfigurationForm onStarted={() => {}} />);

    expect(screen.getByLabelText(/repository url/i)).toHaveValue("");
    expect(screen.getByLabelText(/dependency/i)).toHaveValue("");
    expect(screen.getByLabelText(/risk tolerance/i)).toHaveValue("medium");
  });

  it("seeds every field from a prefill, so correcting one costs only that one", () => {
    // A mistyped path should cost the path, not the dependency, both versions
    // and four constraints as well.
    const prefill: FormPrefill = {
      source: "local",
      url: "",
      path: "/Users/me/Code/payments-service",
      name: "pydantic",
      from: "1.10.13",
      to: "2.9.2",
      zeroDowntime: true,
      minimizeEffort: true,
      deadline: "2026-09-15",
      riskTolerance: "low",
    };

    render(<ConfigurationForm onStarted={() => {}} prefill={prefill} />);

    // The source radio follows the ref kind, so the path input is the one on
    // screen rather than the URL input the run never used.
    expect(screen.getByRole("radio", { name: /local/i })).toBeChecked();
    expect(screen.getByLabelText(/local path/i)).toHaveValue("/Users/me/Code/payments-service");
    expect(screen.getByLabelText(/dependency/i)).toHaveValue("pydantic");
    expect(screen.getByLabelText(/current version/i)).toHaveValue("1.10.13");
    expect(screen.getByLabelText(/target version/i)).toHaveValue("2.9.2");
    expect(screen.getByLabelText(/zero downtime/i)).toBeChecked();
    expect(screen.getByLabelText(/minimize effort/i)).toBeChecked();
    expect(screen.getByLabelText(/deadline/i)).toHaveValue("2026-09-15");
    expect(screen.getByLabelText(/risk tolerance/i)).toHaveValue("low");
  });

  it("leaves a prefilled field editable", async () => {
    // Seeded, not locked: the whole point is to correct one of these.
    const user = userEvent.setup();
    render(
      <ConfigurationForm
        onStarted={() => {}}
        prefill={{
          source: "local",
          url: "",
          path: "/User/Code/payments-service",
          name: "pydantic",
          from: "1.10.13",
          to: "2.9.2",
          zeroDowntime: false,
          minimizeEffort: false,
          deadline: "",
          riskTolerance: "medium",
        }}
      />,
    );

    const input = screen.getByLabelText(/local path/i);
    await user.clear(input);
    await user.type(input, "/Users/me/Code/payments-service");

    expect(input).toHaveValue("/Users/me/Code/payments-service");
  });
});
