import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { ConfirmDialog } from "./ConfirmDialog";

afterEach(() => {
  cleanup();
});

describe("ConfirmDialog", () => {
  const baseProps = {
    open: true,
    title: "Delete item?",
    message: "This action cannot be undone.",
    onConfirm: () => {},
    onCancel: () => {},
  };

  it("renders title and message when open=true", () => {
    render(<ConfirmDialog {...baseProps} />);
    expect(screen.getByText("Delete item?")).toBeInTheDocument();
    expect(screen.getByText("This action cannot be undone.")).toBeInTheDocument();
  });

  it("renders nothing when open=false", () => {
    const { container } = render(<ConfirmDialog {...baseProps} open={false} />);
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByText("Delete item?")).not.toBeInTheDocument();
  });

  it("calls onConfirm when confirm button is clicked", () => {
    const onConfirm = vi.fn();
    render(<ConfirmDialog {...baseProps} onConfirm={onConfirm} />);
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("calls onCancel when cancel button is clicked", () => {
    const onCancel = vi.fn();
    render(<ConfirmDialog {...baseProps} onCancel={onCancel} />);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("calls onCancel when Escape key is pressed", () => {
    const onCancel = vi.fn();
    render(<ConfirmDialog {...baseProps} onCancel={onCancel} />);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("does NOT call onCancel on Escape when open=false", () => {
    const onCancel = vi.fn();
    render(<ConfirmDialog {...baseProps} open={false} onCancel={onCancel} />);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onCancel).not.toHaveBeenCalled();
  });

  it("calls onCancel when backdrop is clicked", () => {
    const onCancel = vi.fn();
    const { container } = render(
      <ConfirmDialog {...baseProps} onCancel={onCancel} />,
    );
    const backdrop = container.querySelector(".confirm-dialog-backdrop");
    expect(backdrop).not.toBeNull();
    fireEvent.click(backdrop as Element);
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("does NOT call onCancel when clicking inside the card", () => {
    const onCancel = vi.fn();
    const { container } = render(
      <ConfirmDialog {...baseProps} onCancel={onCancel} />,
    );
    const card = container.querySelector(".confirm-dialog-card");
    expect(card).not.toBeNull();
    fireEvent.click(card as Element);
    expect(onCancel).not.toHaveBeenCalled();
  });

  it("uses custom confirmLabel and cancelLabel when provided", () => {
    render(
      <ConfirmDialog
        {...baseProps}
        confirmLabel="Delete"
        cancelLabel="Keep"
      />,
    );
    expect(screen.getByRole("button", { name: "Delete" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Keep" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Confirm" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cancel" })).not.toBeInTheDocument();
  });

  it("applies .destructive class to confirm button when destructive=true", () => {
    render(<ConfirmDialog {...baseProps} destructive />);
    const confirmBtn = screen.getByRole("button", { name: "Confirm" });
    expect(confirmBtn).toHaveClass("destructive");
  });

  it("does NOT apply .destructive class when destructive is false/unset", () => {
    render(<ConfirmDialog {...baseProps} />);
    const confirmBtn = screen.getByRole("button", { name: "Confirm" });
    expect(confirmBtn).not.toHaveClass("destructive");
  });
});
