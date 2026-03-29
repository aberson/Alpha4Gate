import { useState, useEffect, useCallback } from "react";
import type { BuildOrder } from "../types/game";

export function useBuildOrders() {
  const [orders, setOrders] = useState<BuildOrder[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchOrders = useCallback(async () => {
    try {
      const resp = await fetch("/api/build-orders");
      const data = await resp.json();
      setOrders(data.orders || []);
    } catch {
      // Ignore fetch errors
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchOrders();
  }, [fetchOrders]);

  const createOrder = async (order: Omit<BuildOrder, "id">) => {
    const resp = await fetch("/api/build-orders", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(order),
    });
    const result = await resp.json();
    await fetchOrders();
    return result;
  };

  const deleteOrder = async (id: string) => {
    await fetch(`/api/build-orders/${id}`, { method: "DELETE" });
    await fetchOrders();
  };

  return { orders, loading, createOrder, deleteOrder, refresh: fetchOrders };
}
