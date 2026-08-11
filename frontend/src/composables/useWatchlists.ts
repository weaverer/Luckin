import { useMutation, useQuery, useQueryClient } from "@tanstack/vue-query";
import { computed } from "vue";

import { apiRequest } from "@/api/client/http";
import { watchlistKeys } from "@/api/query-keys/watchlists";
import type { Stock } from "@/composables/useStocks";

export interface WatchlistMember {
  member_id: string;
  stock: Stock;
  sort_order: number;
}

export interface WatchlistGroup {
  group_id: string;
  name: string;
  notes: string;
  tags: string[];
  sort_order: number;
  members: WatchlistMember[];
}

export interface WatchlistGroupInput {
  name: string;
  notes: string;
  tags: string[];
}

export function useWatchlists() {
  const client = useQueryClient();
  const groupsQuery = useQuery({
    queryKey: watchlistKeys.list(),
    queryFn: () => apiRequest<WatchlistGroup[]>({ url: "/watchlists" }),
  });
  const invalidate = () =>
    client.invalidateQueries({ queryKey: watchlistKeys.all });
  const createMutation = useMutation({
    mutationFn: (input: WatchlistGroupInput) =>
      apiRequest<WatchlistGroup>({
        method: "POST",
        url: "/watchlists",
        data: input,
      }),
    onSuccess: invalidate,
  });
  const updateMutation = useMutation({
    mutationFn: (input: WatchlistGroupInput & { groupId: string }) =>
      apiRequest<WatchlistGroup>({
        method: "PUT",
        url: `/watchlists/${input.groupId}`,
        data: { name: input.name, notes: input.notes, tags: input.tags },
      }),
    onSuccess: invalidate,
  });
  const reorderMutation = useMutation({
    mutationFn: (groupIds: string[]) =>
      apiRequest<WatchlistGroup[]>({
        method: "PUT",
        url: "/watchlists/order",
        data: { group_ids: groupIds },
      }),
    onSuccess: invalidate,
  });
  const deleteMutation = useMutation({
    mutationFn: (groupId: string) =>
      apiRequest<void>({ method: "DELETE", url: `/watchlists/${groupId}` }),
    onSuccess: invalidate,
  });
  const addMutation = useMutation({
    mutationFn: (input: { groupId: string; stockId: string }) =>
      apiRequest<WatchlistMember>({
        method: "POST",
        url: `/watchlists/${input.groupId}/members`,
        data: { stock_id: input.stockId },
      }),
    onSuccess: invalidate,
  });
  const removeMutation = useMutation({
    mutationFn: (input: { groupId: string; stockId: string }) =>
      apiRequest<void>({
        method: "DELETE",
        url: `/watchlists/${input.groupId}/members/${input.stockId}`,
      }),
    onSuccess: invalidate,
  });

  return {
    groups: computed(() => groupsQuery.data.value ?? []),
    loading: groupsQuery.isPending,
    error: computed(() =>
      groupsQuery.error.value ? "自选分组加载失败，请稍后重试" : "",
    ),
    saving: computed(
      () =>
        createMutation.isPending.value ||
        updateMutation.isPending.value ||
        reorderMutation.isPending.value ||
        deleteMutation.isPending.value ||
        addMutation.isPending.value ||
        removeMutation.isPending.value,
    ),
    create: (input: WatchlistGroupInput) => createMutation.mutateAsync(input),
    update: (groupId: string, input: WatchlistGroupInput) =>
      updateMutation.mutateAsync({ groupId, ...input }),
    reorder: (groupIds: string[]) => reorderMutation.mutateAsync(groupIds),
    deleteGroup: (groupId: string) => deleteMutation.mutateAsync(groupId),
    add: (groupId: string, stockId: string) =>
      addMutation.mutateAsync({ groupId, stockId }),
    remove: (groupId: string, stockId: string) =>
      removeMutation.mutateAsync({ groupId, stockId }),
    refresh: groupsQuery.refetch,
  };
}
