import dotenv from "dotenv";
import { z } from "zod";

dotenv.config();

const envSchema = z.object({
  SOLANA_RPC_URL: z.string().url(),
  SOLANA_CLUSTER: z.string().default("mainnet-beta"),
});

export const config = envSchema.parse({
  SOLANA_RPC_URL: process.env.SOLANA_RPC_URL,
  SOLANA_CLUSTER: process.env.SOLANA_CLUSTER ?? "mainnet-beta",
});

export type Config = z.infer<typeof envSchema>;
